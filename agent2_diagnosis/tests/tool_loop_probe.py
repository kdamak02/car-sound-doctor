"""Offline replica of n8n's Tools Agent loop with Ollama (needs Ollama on :11434 and the dev RAG on :8002): system + user + rag_search tool -> tool result -> final answer.
python agent2_diagnosis/tests/tool_loop_probe.py <variant> <runs> [case]   variants: base | suffix | suffix_t0 | suffix_short
Prints per-run: tool calls, final-answer shape, whether the grounded Agent-2 builders keep causes/checks.
"""
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from agent2_diagnosis import agent2  # noqa: E402

variant = sys.argv[1] if len(sys.argv) > 1 else "base"
runs = int(sys.argv[2]) if len(sys.argv) > 2 else 5
case_name = sys.argv[3] if len(sys.argv) > 3 else "clio_starter_pass1"
MODEL = "qwen2.5:7b"

# ---- the exact messages n8n would build (node "Prepare input", tool mode)
JS = r"""
const fs=require('fs'),vm=require('vm');
const read=p=>fs.readFileSync(p,'utf8');const R=%r;
const md=s=>s.split('\r\n').join('\n').match(/## Prompt \(current version\)\s*```text\n([\s\S]*?)```/)[1].trim();
const f=stem=>fs.readdirSync(R+'/prompts').find(x=>x.startsWith(stem+'_'));
const prepare=vm.runInNewContext(read(R+'/n8n/code_nodes/agent2_prepare.js')+'\n;prepare',{});
const c=JSON.parse(read(R+'/agent2_diagnosis/tests/cases/%s.json'));
const out=prepare(c,md(read(R+'/prompts/'+f('P3'))),{1:md(read(R+'/prompts/'+f('P4'))),2:md(read(R+'/prompts/'+f('P5')))},JSON.parse(read(R+'/contracts/labels.json')),'tool');
console.log(JSON.stringify(out));
""" % (str(REPO).replace("\\", "/"), case_name)
prep = json.loads(subprocess.run(["node", "-e", JS], capture_output=True, text=True, encoding="utf-8").stdout)
system, user, filters = prep["system_prompt"], prep["user_message"], json.loads(prep["filters_json"])

TOOL = {"type": "function", "function": {
    "name": "rag_search",
    "description": "Search the car manuals and OBD-II code database. Input: query = a short search text (symptom wording, the likely fault, "
                   "or the driver's answers). Returns passages, each with chunk_id, text, source, page, score, and fallback_level. "
                   "Vehicle filters are added automatically.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "search text, in the driver's language or in English"}},
                   "required": ["query"]}}}

TOP_K = 3
OPTIONS = {"temperature": 0.2, "num_ctx": 8192, "num_predict": 1200}
SYSTEM_SUFFIX = ""
SHORT_RESULT = False
if variant in ("suffix", "suffix_t0", "suffix_short"):
    SYSTEM_SUFFIX = ("\n\nHOW TO WORK:\n1. Call the rag_search tool ONCE with a short query about the driver's problem.\n"
                     "2. When you receive the passages, STOP calling tools and write the final answer.\n"
                     "3. The final answer is ONLY the JSON object described above: no sentence before or after it, no markdown.\n"
                     "Copy every chunk_id exactly as it appears in the passages.")
if variant == "n8n":  # what the n8n workflow runs: prompts as they are in prompts/*.md, temperature 0, 3 passages per search
    OPTIONS["temperature"] = 0
if variant in ("suffix_t0",):
    OPTIONS["temperature"] = 0
if variant in ("suffix_short",):
    SHORT_RESULT = True
    OPTIONS["temperature"] = 0


def post(path, body):
    req = urllib.request.Request("http://localhost:11434" + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=280).read())


def search(query):
    body = json.dumps({"query": query, "filters": filters, "top_k": TOP_K, "rerank": True}).encode()
    res = json.loads(urllib.request.urlopen(urllib.request.Request("http://localhost:8002/search", data=body, headers={"Content-Type": "application/json"}), timeout=60).read())
    if SHORT_RESULT:  # fewer tokens per passage
        for p in res["passages"]:
            p["text"] = p["text"][:600]
            for k in ("section", "make", "model"):
                p.pop(k, None)
    return res


ok = 0
for i in range(runs):
    msgs = [{"role": "system", "content": system + SYSTEM_SUFFIX}, {"role": "user", "content": user}]
    calls, retrieved, final, t0 = 0, {}, None, time.time()
    for step in range(5):
        r = post("/api/chat", {"model": MODEL, "messages": msgs, "tools": [TOOL], "stream": False, "options": OPTIONS})
        m = r["message"]
        msgs.append(m)
        if m.get("tool_calls"):
            for tc in m["tool_calls"]:
                calls += 1
                q = (tc["function"].get("arguments") or {}).get("query", "")
                res = search(q)
                for p in res["passages"]:
                    retrieved[p["chunk_id"]] = p
                msgs.append({"role": "tool", "content": json.dumps(res, ensure_ascii=False), "tool_name": "rag_search"})
            continue
        final = m.get("content", "")
        break
    shape = "none" if final is None else ("json" if re.search(r"\{[\s\S]*\}", final) else "prose")
    causes = checks = 0
    if final and shape == "json":
        try:
            raw = json.loads(re.search(r"\{[\s\S]*\}", final).group(0))
            causes = len(agent2.build_causes(raw.get("causes"), retrieved, "fr"))
            checks = len(agent2.build_checks(raw.get("checks"), retrieved, 1, 5))
        except Exception as e:
            shape = "bad_json"
    good = causes > 0
    ok += good
    print(f"run {i+1}: tools={calls} shape={shape:<8} grounded causes={causes} checks={checks} {time.time()-t0:5.1f}s {'OK' if good else 'FAIL'}", flush=True)
    if not good and final is not None and i < 3:
        print("   final:", re.sub(r"\s+", " ", final)[:260])
print(f"variant={variant}: {ok}/{runs} usable")
