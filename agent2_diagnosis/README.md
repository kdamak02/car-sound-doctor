# Agent 2 — diagnosis and physical checks (group B, owner B2)

LLM + RAG, runs as an AI Agent node in n8n, two passes.

**Input** (from the case record): `vehicle`, `symptom`, `audio` (Agent 1 output), and in pass 2 the
`check_answers`.
**Tool**: `POST /search` on the RAG service with `section: ["symptoms", "causes", "codes", "maintenance"]`.
**Output**: `contracts/agent2_output.schema.json`, every check with a citation of a retrieved chunk.

| Pass | Prompt | Job |
|---|---|---|
| 1 | `prompts/P3_audio_description.md` + `prompts/P4_checks_generation.md` | rank 3-5 causes, ask 3-5 checks the user can do without tools |
| 2 | `prompts/P5_reranking.md` | re-rank with the answers; `conclusive=false` and round < 2 → new checks, else go to Agent 3 |

Rules
- Labels of `contracts/labels.json` that Agent 1 did not train (not in `agent1_audio/results/ast.json`; timing chain, turbo...) can be proposed
  as causes even if the audio model cannot detect them, but only with checks that confirm them.
- If a cause involves brakes, steering or overheating: `safety_stop = true`.
- Guardrails (`agent3_repair/guardrails/checks.py`, n8n `code_nodes/validate_output.js`) run on
  this output too.

Put here: test cases (input case + expected causes) in `tests/cases/`, and any helper script.

## What is in this folder

| Path | What |
|---|---|
| `agent2.py` | the logic in Python (reference + tests): queries, `/search`, LLM call, grounding (only retrieved chunks can be cited), decisions in code (`conclusive`, `safety_stop`, label check, check ids, a contradicted cause is capped), guardrails R1-R3 |
| `build_workflow.py` | generates `n8n/workflows/agent2_diagnosis.json` from the prompts, `labels.json` and `n8n/code_nodes/agent2_*.js` (so the workflow cannot drift from the repo) |
| `../n8n/code_nodes/agent2_*.js` | the same logic as n8n Code nodes: Prepare input, Retrieve passages (prefetch mode), Rescue prompt, Ground and decide |
| `../prompts/P3`, `P4`, `P5` | the prompts, with versions, failure cases and measured before/after |
| `tests/` | pytest (stub LLM and stub RAG), `cases/` (case files + expected behaviour), `eval_dev_rag.py` + `eval_results.md` (retrieval evaluation), `reliability.py`, `tool_loop_probe.py` |
| `dev_rag/` | stand-in `/search` service over the manuals + OBD-II codes (ingestion, hybrid search) until B1's Qdrant service exists |
| `streamlit_app.py` | test page: plays Telegram + Agent 1, calls the n8n webhook, answers the checks |
| `system_classifier/` | symptom -> car system classifier (88.4 % accuracy), trained, not wired in |
| `REQUIREMENTS_CHECK.md` | audit against the course brief and the repo rules |

## How the n8n workflow works

`Webhook` (or `When called by main workflow`) -> `Prepare input` (audio text P3, prompt P4 or P5, filters) -> **`Agent 2`** (AI Agent node with the HTTP tool
`rag_search` = `POST /search`) -> `Ground and decide` -> `Guardrails (R2, R3)` (the team node) -> `Valid?` -> `Output` | `Insufficient`.
With the local model a safety net sits after the agent (`Agent answered?` -> `Rescue prompt` -> `Rescue answer`): if the agent returns nothing, one JSON-mode call answers
from the passages it already retrieved. `MODE=prefetch` (retrieval in a Code node, then one chain call) is a fallback for models that cannot drive the tool loop.

## Run it

```bash
# 1. tests, no network, no key (the CI command)
pytest -q agent2_diagnosis/tests
node n8n/code_nodes/test_agent2_nodes.js

# 2. team default: Gemini + mock RAG from docker compose
python agent2_diagnosis/build_workflow.py          # -> n8n/workflows/agent2_diagnosis.json
docker compose up -d n8n rag-service               # MOCK_MODE=true by default
#    import the JSON in n8n (menu > Import from file), open "Gemini Chat Model", select your credential, activate

# 3. local, no quota: Ollama + the real manuals (Windows launcher: start_all.ps1 in the parent folder)
ollama pull qwen2.5:7b                              # OLLAMA_HOST=0.0.0.0:11434 so Docker can reach it
python agent2_diagnosis/dev_rag/ingest.py structure && python agent2_diagnosis/dev_rag/ingest.py obdex   # manuals in rag/raw/manuals, see dev_rag/manuals.json
DEV_RAG_DEVICE=cpu python -m uvicorn agent2_diagnosis.dev_rag.app:app --host 0.0.0.0 --port 8002        # CPU: leaves the GPU to Ollama
LLM=ollama RAG_URL=http://host.docker.internal:8002/search python agent2_diagnosis/build_workflow.py
#    import it, select the Ollama credential (base URL http://host.docker.internal:11434) on BOTH Ollama nodes, activate

# 4. try it
python agent2_diagnosis/tests/reliability.py agent2_diagnosis/tests/cases/clio_starter_pass1.json 5
streamlit run agent2_diagnosis/streamlit_app.py     # http://localhost:8501

# 5. measure retrieval (needs the service of step 3)
python agent2_diagnosis/tests/make_eval_queries.py && python agent2_diagnosis/tests/eval_dev_rag.py --sections symptoms,causes,codes -v
```

## Decisions that go beyond the contract (review in the PR)
- `safety_stop` follows the **most likely** cause (same as the team guardrail); a less likely risky cause only adds a caution sentence to `message_to_user`. Oil pressure is also a safety word.
- A cause the driver's answers contradict is capped at 0.05 (field `status` of prompt P5, read by the code, not part of `agent2_output`).
- Applied (this branch): Agent 2 also searches the section `maintenance` (measured hit rate 0.92 instead of 0.85 without the reranker, 0.81 with it, see `tests/eval_results.md`). Agent 3 uses the same section: the team should confirm the overlap is fine. To go back, change `SECTIONS` in `agent2.py` and `filters` in `n8n/code_nodes/agent2_prepare.js`.
- Reranker (dev RAG only): `BAAI/bge-reranker-v2-m3` (Apache-2.0, ~2.3 GB, downloaded on first start) scores the best 8 passages on CPU (`DEV_RAG_RERANK=false` turns it off, `DEV_RAG_RERANK_MIN` is the threshold, default 0.03). About 4 s per search on CPU, so a run takes 1.5 to 2.5 minutes on a laptop.
- Citation support check: a cause is kept only if one of its cited passages comes back when its own name is searched (`supported_causes` in `agent2.py`, `supportFilter` in `n8n/code_nodes/agent2_postprocess.js`; `AGENT2_SUPPORT_CHECK=false` / `SUPPORT_CHECK=false` skip it). Limit: it sees the cause name, not the symptom (see `REQUIREMENTS_CHECK.md`).
- Agent 3 hand-over: `AGENT3=true python agent2_diagnosis/build_workflow.py` adds `Final answer?` (conclusive or no checks left) → `To Agent 3` (mapper `n8n/code_nodes/agent2_to_agent3.js`, same rules as `agent2_diagnosis/to_agent3.py`) → `Agent 3` (HTTP `POST :8003/generate`, `AGENT3_URL` to change) → reply `{agent2, agent3}`. Without `AGENT3` the reply stays the plain `agent2_output`. Check by hand: `python agent2_diagnosis/tests/chain_to_agent3.py clio_starter_pass2`.
- Groq option (same shared key as Agent 3): `LLM=groq RAG_URL=http://host.docker.internal:8002/search python agent2_diagnosis/build_workflow.py` builds the workflow with a `Groq Chat Model` node (`openai/gpt-oss-120b`, change with `GROQ_MODEL_A2`); import it and select a Groq credential (menu Credentials > Groq API, paste the key in n8n itself). Python path: `AGENT2_PROVIDER=groq` (model `AGENT2_MODEL`, key from `GROQ_API_KEY` or `agent3_repair/.env`). Never commit the key.
  Groq free-tier limits of the shared key (per model): 8,000 tokens per minute and 200,000 tokens per day for the whole organization; one tool-mode run uses about 7,000 tokens, so roughly 28 runs a day per model and about one run per minute. Measured: `gpt-oss-120b` answered 8 of 8 test runs until its daily budget ran out; `gpt-oss-20b` (separate budget) answered 5 of 10 and is the fallback. When both are exhausted use Ollama (no quota). Check the budget before a demo; do not run test batches on the demo day.
- Agent 1 entry: `AGENT1=true python agent2_diagnosis/build_workflow.py` adds a second webhook `POST /webhook/agent2-diagnose-audio` (multipart: field `audio` = the voice note as is, field `case` = the case record JSON). It calls Agent 1 (`AGENT1_URL`, default `http://host.docker.internal:8001/analyze`, form-data `file` + `context` taken from `symptom.context`, 30 s, 1 retry), puts the answer in `case.audio`, then runs Agent 2. `unusable_audio` → reply `{needs_new_audio, message}` asking for a 5-10 s recording; Agent 1 down or error → the audio block is removed and Agent 2 works from the text only. Start Agent 1 with `.gent1_audio\start_agent1.ps1` (port 8001). Combine with `AGENT3=true` for the whole chain: `curl -X POST localhost:5678/webhook/agent2-diagnose-audio -F audio=@note.ogg -F "case=<case.json"`.
- Test page, full chain: switch on "Chaîne complète" in the sidebar and upload a voice note; the page sends it to `/webhook/agent2-diagnose-audio` and shows Agent 1, Agent 2 and Agent 3 (needs `AGENT1=true AGENT3=true` when building the workflow, Agent 1 on :8001, Agent 3 on :8003). Measured with the synthetic clip on Ollama: pass 1 (Agent 1 + Agent 2, checks asked) then pass 2 (conclusive, Agent 3 guide validated) in about 1.5 to 2 minutes. A real engine recording is still to be tested with group A.
- Ollama build: an agent error (a 7B loop on empty search results) counts as no answer, so the case ends as `insufficient`.
