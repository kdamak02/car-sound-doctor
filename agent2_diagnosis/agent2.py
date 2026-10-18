"""
Agent 2 (diagnosis and checks): reference implementation of the logic. Owner: B2.

    from agent2_diagnosis.agent2 import diagnose
    out = diagnose(case)          # case = contracts/case_record.schema.json, out = agent2_output.schema.json

What it does
  1. builds RAG queries from the case and calls POST /search (sections symptoms, causes, codes, maintenance)
  2. asks the LLM (prompts/P4 on pass 1, P5 on pass 2 and 3; P3 text for the audio) for causes and checks
  3. keeps only what is grounded: every cause and check must cite a chunk_id that /search really returned
  4. decides in code (not in the LLM): label validity, probabilities, `conclusive`, `safety_stop`, check ids
  5. runs the team guardrails (agent3_repair/guardrails/checks.py: R1 schema, R2 citations, R3 safety)

Raises Insufficient when nothing grounded survives: n8n then goes to the "insufficient information" message.
Prompts are read from prompts/*.md (single source of truth); the n8n AI Agent node must use the same text.

Env: RAG_SERVICE_URL (default http://localhost:8002), AGENT2_PROVIDER gemini|claude|ollama|groq (default gemini),
     AGENT2_MODEL, GEMINI_API_KEY / ANTHROPIC_API_KEY / GROQ_API_KEY (groq: also read from agent3_repair/.env, git-ignored).
"""
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent3_repair.guardrails.checks import check as guardrail_check  # noqa: E402
from agent2_diagnosis.labels import load_labels  # noqa: E402

LABELS = load_labels(ROOT)
LABEL_EN = {x["id"]: x["en"] for x in LABELS["labels"]}
KNOWN_LABELS = set(LABEL_EN) | set(LABELS["hypotheses_only"]["ids"])
LABEL_NAMES = {x["id"]: {"fr": x["fr"], "en": x["en"]} for x in LABELS["labels"]}

RAG_URL = os.getenv("RAG_SERVICE_URL", "http://localhost:8002")
PROVIDER = os.getenv("AGENT2_PROVIDER", "gemini")
MODELS = {"gemini": os.getenv("AGENT2_MODEL", "gemini-3.6-flash"), "claude": os.getenv("AGENT2_MODEL", "claude-sonnet-5-5"),
          "ollama": os.getenv("AGENT2_MODEL", "qwen2.5:7b"), "groq": os.getenv("AGENT2_MODEL", "openai/gpt-oss-120b")}
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

SECTIONS = ["symptoms", "causes", "codes", "maintenance"]
MAX_ROUNDS = 2  # case_record.round cap
CONCLUSIVE_PROB, CONCLUSIVE_GAP = 0.6, 0.2
SUPPORT_CHECK = os.getenv("AGENT2_SUPPORT_CHECK", "true").lower() == "true"  # drop causes whose cited passages the reranker does not tie to the cause
CONTRADICTED_MAX = 0.05  # probability cap of a cause the driver's answers contradict (status field of P5)
LEVELS = ["exact_model", "make", "generic", "none"]
# R3 words, same as agent3_repair/guardrails/checks.py, plus oil pressure. Applied to the top cause's name and label.
SAFETY_WORDS = re.compile(r"frein|brake|direction|steering|surchauffe|overheat|temp[ée]rature|oil pressure|"
                          r"pression d.huile", re.I)
DEFAULT_MESSAGE = {"fr": "Pour confirmer, répondez à ces questions :", "en": "To confirm, please answer these questions:"}


class Insufficient(Exception):
    """No grounded answer: the manuals do not support any cause (or the output broke a guardrail)."""


# ---------------------------------------------------------------- prompts

def load_prompt(stem: str) -> str:
    """Text of the first ```text block under '## Prompt (current version)' in prompts/<stem>_*.md."""
    md = next((ROOT / "prompts").glob(f"{stem}_*.md")).read_text("utf-8")
    return re.search(r"## Prompt \(current version\)\s*```text\n(.*?)```", md, re.S).group(1).strip()


def fill(template: str, **values) -> str:
    for k, v in values.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template


def unusualness(score) -> str:
    """anomaly_score is a percentile against HEALTHY engines (0.5 = typical healthy sound), see Agent 1's INTEGRATION.md."""
    return "unknown" if score is None else "very high" if score >= 0.95 else "high" if score >= 0.80 else "normal range"


def audio_reading(audio: dict) -> str:
    """Decision table of Agent 1's integration guide, computed here so a small model does not have to infer it."""
    label, score = audio.get("label") or "", audio.get("anomaly_score")
    normal, lines = label.startswith("normal"), []
    if audio.get("confidence") == "low":
        lines.append("The audio is not conclusive: rely on the driver's description and on the checks more than on the sound.")
    if score is not None:
        if not normal and score >= 0.80:
            lines.append("The sound class and the anomaly score agree: start from the most likely classes.")
        if normal and score < 0.80:
            lines.append("The engine probably sounds healthy: ask whether the noise is intermittent and suggest recording again when it happens.")
        if normal and score >= 0.95:
            hypotheses = ", ".join(i.replace("_", " ") for i in LABELS["hypotheses_only"]["ids"])
            lines.append("Very unusual sound outside the classes the audio model knows: also consider these faults and separate "
                         f"them with checks: {hypotheses}.")
    return " ".join(lines) or "none"


def describe_audio(audio: dict | None, lang: str = "en") -> str:
    """P3: Agent 1 output -> text for the LLM."""
    if not audio or audio.get("status") != "ok":
        return "No usable audio: rely on the user's description only."
    top = audio.get("top3") or []

    def name(label):
        names = LABEL_NAMES.get(label)
        return names.get(lang, names["en"]) if names else str(label)

    pct = lambda p: f"{round(p * 100)} %"  # noqa: E731
    return fill(
        load_prompt("P3"),
        label_name=name(audio.get("label")), prob=pct(top[0]["prob"]) if top else "n/a",
        others=", ".join(f"{name(t['label'])} ({pct(t['prob'])})" for t in top[1:]) or "none",
        confidence=audio.get("confidence"), unusualness=unusualness(audio.get("anomaly_score")),
        summary=(audio.get("description") or {}).get("summary", ""), warnings="; ".join(audio.get("warnings") or []) or "none",
        reading=audio_reading(audio))


# ---------------------------------------------------------------- retrieval

def rag_search(query: str, vehicle: dict, top_k: int = 5, rerank: bool = True) -> dict:
    """POST /search (contracts/rag_search.schema.json)."""
    filters = {k: vehicle[k] for k in ("make", "model", "year") if vehicle.get(k)}
    body = json.dumps({"query": query, "filters": {**filters, "section": SECTIONS}, "top_k": top_k, "rerank": rerank}).encode()
    req = request.Request(RAG_URL.rstrip("/") + "/search", data=body, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=120) as r:  # reranked searches take several seconds on CPU
        return json.loads(r.read())


def queries(case: dict) -> list[str]:
    label = LABEL_EN.get((case.get("audio") or {}).get("label"), "")
    symptom = (case.get("symptom") or {}).get("description", "")
    answers = " ".join(a["answer"] for a in case.get("check_answers") or [])  # pass 2+: answers refine retrieval
    said = (case.get("user_input") or {}).get("text", "")  # the driver's own sentence first: the reranker scores full sentences far better than keywords
    qs = [said, f"{symptom} {label}", f"{label} causes" if label else "", f"{symptom} {answers}" if answers else ""]
    return list(dict.fromkeys(q.strip() for q in qs if q.strip()))


def gather(case: dict, search, k: int = 5):
    """-> (passages sorted by score, worst fallback_level seen)."""
    seen, out, level = set(), [], "exact_model"
    for q in queries(case):
        res = search(q, case.get("vehicle") or {}, k)
        level = max(level, res["fallback_level"], key=LEVELS.index)
        for p in res["passages"]:
            if p["chunk_id"] not in seen:
                seen.add(p["chunk_id"])
                out.append(p)
    return sorted(out, key=lambda p: -p["score"])[: 2 * k], level


# ---------------------------------------------------------------- LLM

def groq_key() -> str:
    """GROQ_API_KEY from the environment, else from Agent 3's git-ignored .env (the team shares one key)."""
    if os.getenv("GROQ_API_KEY"):
        return os.environ["GROQ_API_KEY"]
    env = ROOT / "agent3_repair/.env"
    for line in env.read_text("utf-8").splitlines() if env.exists() else []:
        if line.startswith("GROQ_API_KEY="):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError("GROQ_API_KEY not set (environment or agent3_repair/.env)")


def ask(system: str, user: str) -> str:
    if PROVIDER == "groq":  # OpenAI-compatible endpoint
        body = json.dumps({"model": MODELS["groq"], "temperature": 0.2, "response_format": {"type": "json_object"},
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
        req = request.Request("https://api.groq.com/openai/v1/chat/completions", data=body,
                              headers={"Content-Type": "application/json", "Authorization": "Bearer " + groq_key(),
                                       "User-Agent": "car-sound-doctor-agent2"})
        for attempt in range(4):
            try:
                with request.urlopen(req, timeout=120) as r:
                    return json.loads(r.read())["choices"][0]["message"]["content"]
            except error.HTTPError as e:  # rate limit / overload / JSON mode produced invalid JSON: back off and retry
                if attempt == 3 or e.code not in (400, 429, 500, 503):
                    raise
                time.sleep(3 * 2**attempt)
    if PROVIDER == "ollama":
        body = json.dumps({"model": MODELS["ollama"], "stream": False, "format": "json",
                           "options": {"temperature": 0.2, "num_ctx": 8192},
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
        req = request.Request(OLLAMA_URL.rstrip("/") + "/api/chat", data=body, headers={"Content-Type": "application/json"})
        with request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())["message"]["content"]
    if PROVIDER == "gemini":
        from google import genai
        from google.genai import types

        client = genai.Client()  # keep a reference: a temporary Client is closed on garbage collection
        for attempt in range(4):
            try:
                r = client.models.generate_content(
                    model=MODELS["gemini"], contents=user,
                    config=types.GenerateContentConfig(system_instruction=system, temperature=0.2,
                                                       response_mime_type="application/json"))
                return r.text
            except Exception as e:  # free tier answers 503/429 under load: back off and retry
                if attempt == 3 or getattr(e, "code", 0) not in (429, 500, 503):
                    raise
                time.sleep(3 * 2**attempt)
    import anthropic

    r = anthropic.Anthropic().messages.create(model=MODELS["claude"], max_tokens=1500, temperature=0.2, system=system,
                                              messages=[{"role": "user", "content": user}])
    return r.content[0].text


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


def user_message(case: dict, passages: list[dict], level: str, allow_new: bool) -> str:
    lang = (case.get("user_input") or {}).get("language", "unknown")
    parts = [f"Vehicle: {json.dumps(case.get('vehicle'), ensure_ascii=False)}",
             f"Driver's description: {(case.get('symptom') or {}).get('description', '')}",
             f"Driver's language: {lang}", describe_audio(case.get("audio"), lang),
             f"Retrieval level: {level}", f"Label ids you may use: {sorted(KNOWN_LABELS)}"]
    diagnosis = case.get("diagnosis") or []
    if diagnosis:
        answers = {a["check_id"]: a["answer"] for a in case.get("check_answers") or []}
        asked = [f"{c['id']}: {c['q']} -> {answers.get(c['id'], '(no answer)')}" for d in diagnosis for c in d["checks"]]
        parts += [f"Previous causes: {json.dumps(diagnosis[-1]['causes'], ensure_ascii=False)}",
                  "Checks asked and answers:\n" + "\n".join(asked), f"New checks allowed: {'yes' if allow_new else 'no'}"]
    parts.append("Passages:\n" + "\n\n".join(f"[{p['chunk_id']}] ({p['source']} p.{p['page']}) {p['text']}" for p in passages))
    return "\n".join(parts)


# ---------------------------------------------------------------- grounding and decisions

def clean_id(s) -> str:
    """LLMs add spaces, brackets or quotes around ids: ' [clio4ph1fr_p225_c1]' -> 'clio4ph1fr_p225_c1'."""
    return re.sub(r"^[\s\[('\"]+|[\s\])'\",.;]+$", "", str(s))


def readable(name: str, label, lang: str) -> str:
    """Small models sometimes answer with a machine id ('dead_battery') as the cause name: show the label's name instead."""
    if name in LABEL_NAMES or re.fullmatch(r"[A-Za-z0-9]+(_[A-Za-z0-9]+)+", name):  # exactly a label id, or looks like an id
        names = LABEL_NAMES.get(name) or LABEL_NAMES.get(label)
        return names.get(lang, names["en"]) if names else name.replace("_", " ")
    return name


def supported_causes(raw, by_id: dict, case: dict, search, top: int = 3) -> list:
    """A citation that exists is not a citation that supports. For the `top` likeliest causes, search the cause's own name plus the symptom:
    the service reranks and drops weak passages, so the cause stays only if one of the passages it cites comes back.
    ponytail: one extra search per cause (cheap with a GPU reranker); upgrade = score each (cause, cited chunk) pair directly."""
    ranked = sorted(raw or [], key=lambda c: -(c.get("prob") or 0) if isinstance(c.get("prob"), (int, float)) else 0)
    hint = (case.get("symptom") or {}).get("description", "")  # a bare cause name ("Alternateur") only matches code tables
    keep = []
    for n, c in enumerate(ranked):
        ids = {i for i in (clean_id(x) for x in c.get("sources") or []) if i in by_id}
        if n < top and ids and c.get("name"):
            back = {p["chunk_id"] for p in search(f'{c["name"]} {hint}'.strip(), case.get("vehicle") or {}, 8)["passages"]}
            if not ids & back:
                continue  # cited passages do not talk about this cause
        keep.append(c)
    return keep


def build_causes(raw, by_id: dict, lang: str = "fr") -> list[dict]:
    out = []
    for c in raw or []:
        ids = [i for i in (clean_id(x) for x in c.get("sources") or []) if i in by_id]
        if not ids or not c.get("name"):
            continue  # uncited cause: dropped
        try:
            prob = max(float(c.get("prob", 0)), 0.0)
        except (TypeError, ValueError):
            prob = 0.0
        if str(c.get("status", "")).lower() == "contradicted":  # P5: an answer shows it is not the cause
            prob = min(prob, CONTRADICTED_MAX)
        label = c.get("label") if c.get("label") in KNOWN_LABELS else None
        out.append({"name": readable(str(c["name"]).strip(), label, lang), "label": label, "prob": prob,
                    "evidence": [str(e) for e in c.get("evidence") or []] + [f"passage {i}" for i in ids]})
    out = sorted(out, key=lambda c: -c["prob"])[:5]
    total = sum(c["prob"] for c in out) or 1
    for c in out:
        c["prob"] = round(c["prob"] / total, 3)
    return out


def build_checks(raw, by_id: dict, first_n: int, limit: int) -> list[dict]:
    out = []
    for c in raw or []:
        p = by_id.get(clean_id(c.get("source", "")))
        if not p or not c.get("q") or len(out) >= limit:
            continue  # uncited check: dropped
        kind = c.get("answer_type") if c.get("answer_type") in ("yes_no", "choice", "free_text") else "yes_no"
        choices = [str(x) for x in c.get("choices") or []]
        if kind == "choice" and not choices:
            kind = "free_text"
        chk = {"id": f"c{first_n + len(out)}", "q": c["q"], "why": c.get("why", ""), "answer_type": kind,
               "citation": {"chunk_id": p["chunk_id"], "source": p["source"], "page": p["page"], "verified": True}}
        if kind == "choice":
            chk["choices"] = choices
        out.append(chk)
    return out


def is_conclusive(causes: list[dict]) -> bool:
    second = causes[1]["prob"] if len(causes) > 1 else 0.0
    return causes[0]["prob"] >= CONCLUSIVE_PROB and causes[0]["prob"] - second >= CONCLUSIVE_GAP


# ---------------------------------------------------------------- main entry

def diagnose(case: dict, llm=ask, search=rag_search) -> dict:
    """Pass number = case.round + 1 (round = check rounds already answered). Returns agent2_output."""
    n = (case.get("round") or 0) + 1
    allow_new = n <= MAX_ROUNDS  # round < 2
    passages, level = gather(case, search)
    if not passages:
        raise Insufficient("no manual passages found")
    by_id = {p["chunk_id"]: p for p in passages}

    system = load_prompt("P4" if n == 1 else "P5")
    raw = parse_json(llm(system, user_message(case, passages, level, allow_new)))
    # the support check searches without the reranker threshold: a long table chunk can score low for the cause query and a check that
    # vetoes correct answers is worse than a looser one; the cited passage must still come back in the top 8 for the cause + the symptom
    plain = (lambda q, v, k: rag_search(q, v, k, rerank=False)) if search is rag_search else search
    raw_causes = supported_causes(raw.get("causes"), by_id, case, plain) if SUPPORT_CHECK else raw.get("causes")
    causes = build_causes(raw_causes, by_id, (case.get("user_input") or {}).get("language") or "fr")
    if not causes:
        raise Insufficient("the passages support no cause (or the model cited none)")

    asked = sum(len(d["checks"]) for d in case.get("diagnosis") or [])
    checks = build_checks(raw.get("checks"), by_id, asked + 1, 5) if allow_new else []
    conclusive = False if (n == 1 and checks) else is_conclusive(causes)
    if conclusive:
        checks = []  # contract: empty when conclusive
    lang = ((case.get("user_input") or {}).get("language") or "fr")
    message = raw.get("message_to_user") if isinstance(raw.get("message_to_user"), str) else ""
    out = {"pass": n, "causes": causes, "checks": checks, "conclusive": conclusive,
           "message_to_user": message or (DEFAULT_MESSAGE.get(lang, DEFAULT_MESSAGE["en"]) if checks else ""),
           # most likely cause decides (same as the team guardrail); less likely risky causes get a caution in the message (P4/P5)
           "safety_stop": bool(SAFETY_WORDS.search(f"{causes[0]['name']} {causes[0]['label'] or ''}")
                               or causes[0]["label"] == "worn_brakes")}
    problems = guardrail_check("agent2_output", out, by_id)
    if problems:
        raise Insufficient("guardrails: " + "; ".join(problems))
    return out
