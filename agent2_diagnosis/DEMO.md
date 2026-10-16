# Agent 2 demo script (about 5 minutes, B2)

Goal: show a real **AI Agent node calling a RAG tool**, answers **with citations**, a **second pass that changes the ranking**, and an **honest refusal**. Every claim below is tied to a grading criterion.

## Before the demo (10 minutes, in this order)
1. Docker Desktop running, then `.\local\start_all.ps1` (n8n :5678, Ollama :11434, dev RAG :8002, Streamlit :8501). The dev RAG needs about 1 minute to load; check `http://localhost:8002/health` shows `"rerank":true`.
2. n8n: workflow "Agent 2 - diagnosis and checks" is **active** (Ollama build: `LLM=ollama RAG_URL=http://host.docker.internal:8002/search python agent2_diagnosis/build_workflow.py`, import, activate).
3. Warm up: run `clio_starter_pass1.json` once (first call loads the models, 1 to 2 minutes). Do not restart anything after this.
4. Open: n8n workflow canvas (tab 1), Streamlit page (tab 2), `tests/eval_results.md` (tab 3), a pre-recorded run (screen recording of the same scenario, tab 4: the fallback).
5. A run takes 1.5 to 2.5 minutes with CPU reranking. If the room is short on time use `DEV_RAG_RERANK=false` (restart the dev RAG before the demo, never during): runs take about 40 s, and say so.

## Script
| Time | Show | Say |
|---|---|---|
| 0:00 | Slide or canvas: Telegram → n8n → Agent 1 / **Agent 2** / Agent 3 | "Agent 1 hears the engine and gives a label with a probability. I am Agent 2: I turn that into ranked causes and physical checks the driver can do, always backed by a manual page." |
| 0:30 | n8n canvas of Agent 2: Prepare input → **AI Agent** (Ollama qwen2.5:7b) + **rag_search** tool → Ground and decide → team guardrails | "A real tool-calling agent. The model decides what to search; the tool is the shared `/search` service. Everything after the agent is code, not model: citations, probabilities and `safety_stop` are decided there." |
| 1:00 | Streamlit: case **clio_starter_pass1** (Clio 2015 diesel, click at start, battery light; Agent 1 says `dead_battery` 0.70). Run it. | "French input, French answer. Agent 1's reading goes in with a guide: how much to trust it." |
| 1:30 (while it runs, about 1 to 2 min) | Canvas, execution view: the agent's tool calls to `rag_search` | "Here are the searches the agent made. The answer can only cite passages that came back from these calls." |
| 2:30 | Result: causes with probabilities, 2 checks, each with page citation (Clio manual p. 225) | "Not conclusive on purpose in pass 1: it asks two checks before committing. Every check carries `source`, `page`, `verified: true`." |
| 3:00 | Same case, **pass 2** (`clio_starter_pass2`: battery new, terminals clean) | "The driver's answers go back in. The terminals cause drops to 0.15 or less because an answer contradicts it, and the ranking changes. That is prompt P5, with a code cap so a small model cannot ignore it." |
| 3:45 | Case **peugeot208_charge_light_pass1** (208, battery charge light on; Agent 1 says `alternator` 0.58) | "Another manual, another make: the same agent, a different knowledge source." |
| 4:15 | Result: alternator / weak battery, checks citing the 208 manual p. 14 | "Cited again, with the page. The reranker (bge-reranker-v2-m3) filters weak passages: in the evaluation it rejects 3 of 4 out-of-corpus questions against 1 of 4 with dense retrieval alone." |
| 4:30 | `eval_results.md` table | "RAG numbers on 30 queries I wrote: chunking by heading beats fixed windows, BM25 + dense beats dense alone, hit rate 0.85 to 0.92 with the maintenance section. Reranker trades 4 points of hit rate for rejection. The set is small and written by one person, so I treat the numbers as indicative." |
| 5:00 | End | "What is not mine: the Qdrant store and GPU reranker (B1) and the other two agents." |

## If something breaks
- **n8n returns "Error in workflow"** (the 7B model can loop): run it once more; the workflow then answers `insufficient` (no invented answer).
- **Run takes more than 3 minutes**: switch to the pre-recorded run (tab 4) and say it is a recording.
- **Docker or n8n down**: Streamlit alone does not work; show the recording and run `pytest agent2_diagnosis/tests` and `node n8n/code_nodes/test_agent2_nodes.js` live (no network, a few seconds) to show the logic is tested.
- **Never** restart n8n, Ollama or the dev RAG mid-demo.

## Likely questions (short answers)
- *Why a multi-agent design, not one agent?* Three different jobs and data: audio model (A), diagnosis with manuals (me), repair guide with procedures (C). Separate prompts, tools and evaluation; Agent 2's output is a fixed contract (`agent2_output.schema.json`) checked by code.
- *Why a cross-encoder reranker?* Dense scores do not separate "no answer" from "weak answer"; the reranker reads query and passage together. Measured: rejection 1/4 to 3/4, cost 0.846 to 0.808 hit rate.
- *Why not trust the citation?* A citation that exists is not a citation that supports. Code only accepts passages the tool returned, and a second search checks that a cause's cited passage comes back for that cause. Known limit: it checks the cause name, not the symptom.
- *Why a 7B local model?* The free Gemini tier gives about 5 runs a day and billing was not an option. Same workflow runs on Gemini (`LLM=gemini`).
- *Where is the multimodality?* Agent 1's audio label and description enter Agent 2's prompt (P3 reading guide, anomaly score); Telegram voice and text both reach it.
- *What if the sound is normal?* Reading guide: `normal` with a low anomaly score means "probably healthy", and the answer asks whether the noise is intermittent instead of inventing a fault.
