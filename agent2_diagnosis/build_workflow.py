"""
Build n8n/workflows/agent2_diagnosis.json (Agent 2 in n8n, with the RAG /search service). Owner: B2.

    python agent2_diagnosis/build_workflow.py                         # team default: Gemini + tool-calling agent
    LLM=ollama python agent2_diagnosis/build_workflow.py              # local model, no quota: retrieve first, then one JSON call

Two modes (MODE, default tool):
  tool      an n8n AI Agent node with `rag_search` (HTTP tool) decides what to search. Needs a strong tool-calling model
            (Gemini Flash works).
  prefetch  fallback if a model cannot drive the tool loop: a Code node calls /search (same contract), then a Basic LLM Chain
            answers once, in JSON mode, with the passages in its message. The guardrails are identical in both modes.
Tool mode with Ollama qwen2.5:7b needs the working procedure at the end of prompts P4/P5, temperature 0 and one GPU-free
embedding service (DEV_RAG_DEVICE=cpu): without them 0 of about 25 runs were usable, with them 12 of 12 offline.

The workflow is generated so that it cannot drift from the repo: prompts P3/P4/P5 come from prompts/*.md,
labels from contracts/labels.json, the Code nodes from n8n/code_nodes/*.js. Re-run it after changing any of them,
then import the JSON in n8n (menu > Import from file). No credential is stored in the file: after the import, open the
model node and select your credential (Google Gemini (PaLM) API, or Ollama with the base URL of your Ollama server).

Env: LLM gemini|ollama, MODE tool|prefetch, RESCUE true|false (ollama tool mode: safety net for an empty agent answer), RAG_URL (default http://rag-service:8002/search; a search service running on the
host: http://host.docker.internal:8002/search), RAG_TOP_K (passages per search, default 5 in tool mode, 3 in prefetch),
OLLAMA_MODEL (default qwen2.5:7b).

Flow:  Webhook / called by main workflow -> Prepare input -> [Retrieve passages] -> Agent 2 -> Ground and decide
       -> Guardrails (team validate_output.js) -> Valid? -> Output | Insufficient
Test alone:  curl -X POST http://localhost:5678/webhook/agent2-diagnose -H "Content-Type: application/json" \
             -d @agent2_diagnosis/tests/cases/clio_starter_pass1.json   (use /webhook-test/ while the workflow is open in the editor)
"""
import json
import os
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NS = uuid.UUID("a2a2a2a2-0000-4000-8000-000000000002")

LLM = os.getenv("LLM", "gemini")  # gemini (team default) | ollama (local, no quota) | groq (shared team key)
MODE = os.getenv("MODE", "tool")
RAG_URL = os.getenv("RAG_URL", "http://rag-service:8002/search")  # docker compose service name
TOP_K = int(os.getenv("RAG_TOP_K", "3" if LLM == "ollama" else "5"))  # a small local model has a short context window
MAX_PASSAGES = 6  # prefetch mode: passages kept in the prompt (small context window)


def prompt(stem):
    md = next((ROOT / "prompts").glob(f"{stem}_*.md")).read_text("utf-8")
    return re.search(r"## Prompt \(current version\)\s*```text\n(.*?)```", md, re.S).group(1).strip()


def src(name):
    return (ROOT / "n8n/code_nodes" / name).read_text("utf-8").replace("\r\n", "\n")


def uid(name):
    return str(uuid.uuid5(NS, name))


sys.path.insert(0, str(ROOT))
from agent2_diagnosis.labels import load_labels  # noqa: E402

LABELS = load_labels(ROOT)  # both formats of labels.json
HEADER = (f"const P3 = {json.dumps(prompt('P3'))};\n"
          f"const SYSTEM = {json.dumps({1: prompt('P4'), 2: prompt('P5')})};\n"
          f"const LABELS = {json.dumps(LABELS, ensure_ascii=False)};\n\n")

PREPARE = (HEADER + src("agent2_prepare.js") +
           f"\nreturn {{ json: prepare($json.body || $json, P3, SYSTEM, LABELS, '{MODE}') }};\n")
RETRIEVE = (f"const RAG_URL = {json.dumps(RAG_URL)};\nconst TOP_K = {TOP_K};\n\n" + src("agent2_retrieve.js") +
            "\nconst search = (query, filters, top_k) => this.helpers.httpRequest({\n"
            "  method: 'POST', url: RAG_URL, body: { query, filters, top_k, rerank: true }, json: true });\n"
            f"return {{ json: await retrieve({{ ...$json, top_k: TOP_K }}, search, {MAX_PASSAGES}) }};\n")
if MODE == "prefetch":  # Basic LLM Chain answers in `text`; the passages come from the "Retrieve passages" node
    AGENT_OUT = "{ output: $json.text ?? $json.response ?? $json.output, intermediateSteps: $('Retrieve passages').first().json.observations }"
else:                   # AI Agent node: tool results are in intermediateSteps
    AGENT_OUT = "{ output: $json.output, intermediateSteps: $json.intermediateSteps }"
AGENT_STEPS_JS = "(() => { try { const o = $('Rescue prompt').first().json.observations; if (o) return o; } catch (e) {} return $('Agent 2').first().json.intermediateSteps || []; })()"
if os.getenv("LLM", "gemini") == "ollama" and os.getenv("MODE", "tool") == "tool" and os.getenv("RESCUE", "true").lower() == "true":
    # the answer comes either from the agent (`output`) or from the rescue chain (`text`); the passages are always the agent's
    AGENT_OUT = "{ output: $json.output !== undefined ? $json.output : $json.text, intermediateSteps: " + AGENT_STEPS_JS + " }"
SUPPORT = os.getenv("SUPPORT_CHECK", "true").lower()  # citation support check through the reranker; SUPPORT_CHECK=false skips it
POSTPROCESS = (f"const SUPPORT = {SUPPORT};\nconst LABELS = {json.dumps(LABELS, ensure_ascii=False)};\n\n" + src("agent2_postprocess.js") +
               "\nconst ctx = $('Prepare input').first().json.ctx;\n"
               "const search = (query, filters, top_k) => this.helpers.httpRequest({\n"
               f"  method: 'POST', url: {json.dumps(RAG_URL)}, body: {{ query, filters, top_k, rerank: true }}, json: true }});\n"
               f"const answer = {AGENT_OUT};\n"
               "const checked = SUPPORT ? await supportFilter(answer, search, JSON.parse($('Prepare input').first().json.filters_json), 3, ($('Prepare input').first().json.queries || [''])[0]) : answer;\n"
               "return { json: postprocess(ctx, checked, LABELS) };\n")
RESCUE_ON = LLM == "ollama" and MODE == "tool" and os.getenv("RESCUE", "true").lower() == "true"
RESCUE = (f"const RAG_URL = {json.dumps(RAG_URL)};\nconst TOP_K = {TOP_K};\n\n" + src("agent2_rescue.js") + "\n" + src("agent2_retrieve.js") +
          "\nconst search = (query, filters, top_k) => this.helpers.httpRequest({\n"
          "  method: 'POST', url: RAG_URL, body: { query, filters, top_k, rerank: true }, json: true });\n"
          "const prep = $('Prepare input').first().json;\n"
          "let steps = $('Agent 2').first().json.intermediateSteps || [];\n"
          "// the agent got no passages (a 7B loop, or a search that returned nothing): search here instead, the answer is still grounded in /search\n"
          f"const has = steps.some((s) => {{ let o = s.observation; if (typeof o === 'string') {{ try {{ o = JSON.parse(o); }} catch (e) {{ return false; }} }} return !!o && Array.isArray(o.passages) && o.passages.length > 0; }});\n"
          f"if (!has) steps = (await retrieve({{ ...prep, top_k: TOP_K }}, search, {MAX_PASSAGES})).observations;\n"
          f"return {{ json: {{ ...rescue(prep, steps, {MAX_PASSAGES}), observations: steps }} }};\n")
GUARDRAILS = src("validate_output.js")  # the team's node, unchanged
OUTPUT = "return { json: $json.output };\n"
INSUFFICIENT = ("const reason = $json.reason || ($json.problems || []).join('; ');\n"
                "return { json: { insufficient: true, reason, problems: $json.problems || [] } };\n")

TOOL_BODY = ('={\n  "query": "{query}",\n  "filters": {{ $(\'Prepare input\').first().json.filters_json }},\n'
             f'  "top_k": {TOP_K},\n  "rerank": true\n}}')


def node(name, type_, version, params, pos, **extra):
    return {"id": uid(name), "name": name, "type": type_, "typeVersion": version, "position": pos,
            "parameters": params, **extra}


def code(name, js, pos):
    return node(name, "n8n-nodes-base.code", 2, {"mode": "runOnceForEachItem", "jsCode": js}, pos)


def link(to, kind="main", index=0):
    return {kind: [[{"node": to, "type": kind, "index": index}]]}


if LLM == "ollama":
    LLM_NAME = "Ollama Chat Model"
    options = {"temperature": 0, "numCtx": 8192, "numPredict": 1200}  # temperature 0: the 7B tool loop is stable only when it is deterministic
    if MODE == "prefetch":
        options["format"] = "json"  # Ollama JSON mode: the answer is always a valid JSON object
    LLM_NODE = node(LLM_NAME, "@n8n/n8n-nodes-langchain.lmChatOllama", 1,
                    {"model": os.getenv("OLLAMA_MODEL", "qwen2.5:7b"), "options": options}, [440, 340])
elif LLM == "groq":
    LLM_NAME = "Groq Chat Model"  # credential "Groq account": Groq API key (same key as Agent 3)
    LLM_NODE = node(LLM_NAME, "@n8n/n8n-nodes-langchain.lmChatGroq", 1,
                    {"model": os.getenv("GROQ_MODEL_A2", "openai/gpt-oss-120b"), "options": {"temperature": 0.2}}, [440, 340])
else:
    LLM_NAME = "Gemini Chat Model"
    LLM_NODE = node(LLM_NAME, "@n8n/n8n-nodes-langchain.lmChatGoogleGemini", 1,
                    {"modelName": "models/gemini-3.6-flash", "options": {"temperature": 0.2}}, [440, 340])

TAIL = [
    code("Ground and decide", POSTPROCESS, [820, 100]),
    code("Guardrails (R2, R3)", GUARDRAILS, [1080, 100]),
    node("Valid?", "n8n-nodes-base.if", 2.2,
         {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                         "conditions": [{"id": uid("cond"), "leftValue": "={{ $json.valid && !$json.insufficient }}",
                                         "rightValue": "", "operator": {"type": "boolean", "operation": "true",
                                                                        "singleValue": True}}], "combinator": "and"},
          "options": {}}, [1320, 100]),
    code("Output", OUTPUT, [1560, 0]),
    code("Insufficient", INSUFFICIENT, [1560, 220]),
]
HEAD = [
    node("Webhook", "n8n-nodes-base.webhook", 2,
         {"httpMethod": "POST", "path": "agent2-diagnose", "responseMode": "lastNode", "options": {}}, [0, 0],
         webhookId=uid("webhook")),
    node("When called by main workflow", "n8n-nodes-base.executeWorkflowTrigger", 1.1,
         {"inputSource": "passthrough"}, [0, 200]),
    code("Prepare input", PREPARE, [260, 100]),
]
CONN_TAIL = {
    "Ground and decide": link("Guardrails (R2, R3)"),
    "Guardrails (R2, R3)": link("Valid?"),
    "Valid?": {"main": [[{"node": "Output", "type": "main", "index": 0}],
                        [{"node": "Insufficient", "type": "main", "index": 0}]]},
}
CONN_HEAD = {"Webhook": link("Prepare input"), "When called by main workflow": link("Prepare input")}

if MODE == "prefetch":
    nodes = HEAD + [
        code("Retrieve passages", RETRIEVE, [520, 100]),
        node("Agent 2", "@n8n/n8n-nodes-langchain.chainLlm", 1.5,  # one JSON answer; the prompt already holds system + passages
             {"promptType": "define", "text": "={{ $json.full_prompt }}", "hasOutputParser": False}, [680, 100]),
        LLM_NODE] + TAIL
    connections = {**CONN_HEAD, "Prepare input": link("Retrieve passages"), "Retrieve passages": link("Agent 2"),
                   "Agent 2": link("Ground and decide"), LLM_NAME: link("Agent 2", "ai_languageModel"), **CONN_TAIL}
else:
    nodes = HEAD + [
        node("Agent 2", "@n8n/n8n-nodes-langchain.agent", 1.7,
             {"promptType": "define", "text": "={{ $json.user_message }}", "hasOutputParser": False,
              "options": {"systemMessage": "={{ $json.system_prompt }}", "maxIterations": 4, "returnIntermediateSteps": True}},
             [520, 100], **({"onError": "continueRegularOutput"} if RESCUE_ON else {})),  # a 7B loop on empty search results ends in an error: treat it as "no answer", the rescue path then ends as insufficient
        LLM_NODE,
        node("rag_search", "@n8n/n8n-nodes-langchain.toolHttpRequest", 1.1,
             {"toolDescription": "Search the car manuals and OBD-II code database. Input: query = one full sentence "
                                 "in the driver's own words (the symptom, the likely fault, or the driver's answers). Returns passages, each with "
                                 "chunk_id, text, source, page, score, and fallback_level. Vehicle filters are added automatically.",
              "method": "POST", "url": RAG_URL, "sendBody": True, "specifyBody": "json", "jsonBody": TOOL_BODY,
              "placeholderDefinitions": {"values": [{"name": "query", "description": "search text, in the driver's language "
                                                     "or in English", "type": "string"}]}}, [640, 340])] + TAIL
    connections = {**CONN_HEAD, "Prepare input": link("Agent 2"), "Agent 2": link("Ground and decide"),
                   LLM_NAME: link("Agent 2", "ai_languageModel"), "rag_search": link("Agent 2", "ai_tool"), **CONN_TAIL}

if RESCUE_ON:  # empty agent answer -> one JSON-mode call with the passages the agent already retrieved
    nodes += [
        node("Agent answered?", "n8n-nodes-base.if", 2.2,
             {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                             "conditions": [{"id": uid("cond-answered"), "leftValue": "={{ String($json.output || '').trim().length > 0 }}",
                                             "rightValue": "", "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
                             "combinator": "and"}, "options": {}}, [700, 100]),
        code("Rescue prompt", RESCUE, [820, 260]),
        node("Rescue answer", "@n8n/n8n-nodes-langchain.chainLlm", 1.5,
             {"promptType": "define", "text": "={{ $json.full_prompt }}", "hasOutputParser": False}, [980, 260]),
        node("Ollama JSON (rescue)", "@n8n/n8n-nodes-langchain.lmChatOllama", 1,
             {"model": os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
              "options": {"temperature": 0, "numCtx": 8192, "numPredict": 1200, "format": "json"}}, [980, 420]),
    ]
    connections["Agent 2"] = link("Agent answered?")
    connections["Agent answered?"] = {"main": [[{"node": "Ground and decide", "type": "main", "index": 0}],
                                               [{"node": "Rescue prompt", "type": "main", "index": 0}]]}
    connections["Rescue prompt"] = link("Rescue answer")
    connections["Rescue answer"] = link("Ground and decide")
    connections["Ollama JSON (rescue)"] = link("Rescue answer", "ai_languageModel")

if os.getenv("AGENT3", "false").lower() == "true":  # hand the final answer to Agent 3 (repair guide); reply = {agent2, agent3}
    TO_A3 = (src("agent2_to_agent3.js") + "\nlet c;\ntry { c = $('Webhook').first().json.body; } catch (e) { c = null; }\n"
             "if (!c) c = $('When called by main workflow').first().json;\n"
             "return { json: { case_record: toAgent3(c, $json), agent2: $json } };\n")
    nodes += [
        node("Final answer?", "n8n-nodes-base.if", 2.2,
             {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                             "conditions": [{"id": uid("cond-final"), "leftValue": "={{ $json.conclusive === true || ($json.checks || []).length === 0 }}",
                                             "rightValue": "", "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
                             "combinator": "and"}, "options": {}}, [1800, 0]),
        code("To Agent 3", TO_A3, [2040, -60]),
        node("Agent 3", "n8n-nodes-base.httpRequest", 4.2,
             {"method": "POST", "url": os.getenv("AGENT3_URL", "http://host.docker.internal:8003/generate"), "sendBody": True,
              "specifyBody": "json", "jsonBody": "={{ JSON.stringify({ case_record: $json.case_record, retrieve: true }) }}",
              "options": {"timeout": 150000}}, [2280, -60], onError="continueRegularOutput"),
        code("Reply (Agent 2 + Agent 3)", "return { json: { agent2: $('To Agent 3').first().json.agent2, agent3: $json } };" + "\n", [2520, -60]),
        code("Reply (Agent 2 only)", "return { json: { agent2: $json, agent3: null } };" + "\n", [2040, 100]),
    ]
    connections["Output"] = link("Final answer?")
    connections["Final answer?"] = {"main": [[{"node": "To Agent 3", "type": "main", "index": 0}],
                                             [{"node": "Reply (Agent 2 only)", "type": "main", "index": 0}]]}
    connections["To Agent 3"] = link("Agent 3")
    connections["Agent 3"] = link("Reply (Agent 2 + Agent 3)")

workflow = {"name": "Agent 2 - diagnosis and checks", "nodes": nodes, "connections": connections,
            "active": False, "settings": {"executionOrder": "v1"}, "tags": [], "pinData": {}}

if __name__ == "__main__":
    out = ROOT / "n8n/workflows/agent2_diagnosis.json"
    out.write_text(json.dumps(workflow, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", out.relative_to(ROOT), f"({len(nodes)} nodes, mode={MODE}, llm={LLM})")
