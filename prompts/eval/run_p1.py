"""Evaluation of prompt P1 (extraction of car / symptom / context from the driver's first message).

    python prompts/eval/run_p1.py v0 v1 v2 [--model qwen2.5:7b] [--show]

v0 = the deterministic parser of the Telegram front (regex, n8n/telegram_front/telegram_front.js: parseCar + guessContext), run with node
v1, v2 = the versions of the prompt in prompts/P1_extraction.md (text between the markers of prompts/eval/p1_prompts.py), called on Ollama
Scores: share of the 5 fields (make, model, year, fuel, context) that match, over the 22 cases of p1_cases.jsonl; JSON validity; the injection case (21).
"""
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from urllib import request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from p1_prompts import PROMPTS  # noqa: E402

CASES = [json.loads(l) for l in (HERE / "p1_cases.jsonl").read_text("utf-8").splitlines() if l.strip()]
FIELDS = ("make", "model", "year", "fuel", "context")
args = [a for a in sys.argv[1:] if not a.startswith("--")]
MODEL = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "qwen2.5:7b"
if "--model" in sys.argv:
    args = [a for a in args if a != MODEL]


def norm(x):
    s = unicodedata.normalize("NFKD", str(x or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]", "", s).strip()


def same(field, got, want):
    if field == "year":
        return (got in (None, "", 0)) == (want is None) and (want is None or int(got or 0) == want)
    if field == "context":
        return norm(got or "unknown") == norm(want or "unknown")
    if want is None:
        return norm(got) == ""
    g, w = norm(got), norm(want)
    return bool(g) and (g == w or g in w or w in g) if field == "model" else g == w


def v0(text):
    js = ("const fs=require('fs'),vm=require('vm');const api=vm.runInNewContext(fs.readFileSync(%s,'utf8')+';({parseCar,guessContext})',{});"
          "const t=fs.readFileSync(0,'utf8');const c=api.parseCar(t);console.log(JSON.stringify({car:c,context:api.guessContext(t)}))"
          % json.dumps(str(ROOT / "n8n/telegram_front/telegram_front.js")))
    # the text goes through stdin as UTF-8 (a command-line argument is mangled by the Windows code page)
    out = json.loads(subprocess.run(["node", "-e", js], input=text.encode("utf-8"), capture_output=True, check=True).stdout.decode("utf-8"))
    c = out["car"] or {}
    return {"make": c.get("make"), "model": c.get("model"), "year": c.get("year"), "fuel": c.get("fuel"), "context": out["context"] or "unknown"}


def llm(version, text):
    body = json.dumps({"model": MODEL, "stream": False, "format": "json", "options": {"temperature": 0, "num_ctx": 4096},
                       "messages": [{"role": "system", "content": PROMPTS[version]}, {"role": "user", "content": text}]}).encode()
    req = request.Request("http://localhost:11434/api/chat", data=body, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=300) as r:
        raw = json.loads(r.read())["message"]["content"]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


SKIP = {int(x) for x in sys.argv[sys.argv.index("--skip") + 1].split(",")} if "--skip" in sys.argv else set()  # cases that are also examples in a prompt
if "--skip" in sys.argv:
    args = [a for a in args if a != sys.argv[sys.argv.index("--skip") + 1]]

for version in args:
    ok = tot = valid = 0
    wrong = []
    for c in [c for c in CASES if c["id"] not in SKIP]:
        got = v0(c["text"]) if version == "v0" else llm(version, c["text"])
        valid += got is not None
        got = got or {}
        for f in FIELDS:
            tot += 1
            hit = same(f, got.get(f), c[f])
            ok += hit
            if not hit:
                wrong.append((c["id"], f, got.get(f), c[f]))
    print(f"\n== {version}: {ok}/{tot} fields correct ({round(100 * ok / tot)} %), valid JSON {valid}/{len(CASES) - len(SKIP)}")
    if "--show" in sys.argv:
        for w in wrong:
            print("   case %s field %-7s got %-14r expected %r" % w)
    inj = (llm(version, CASES[20]["text"]) if version != "v0" else v0(CASES[20]["text"])) or {}
    print("   injection case 21 ->", {k: inj.get(k) for k in ("make", "model", "year", "context")})
