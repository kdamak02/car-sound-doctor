# Requirements check for Agent 2 (B2) — 2026-10-15

Sources: the course brief (`Advanced DL-Exam.pdf`: 5 mandatory requirements, deliverables, grading) and the team rules of this repo
(README, CONTRIBUTING, `agent2_diagnosis/README.md`, `report/README.md`). Status: OK = met and evidenced, PARTIAL = met with a limit,
OPEN = not done, TEAM = depends on other groups.

## A. The five mandatory requirements of the brief

| # | Requirement | Status | Evidence | Gap / what is left |
|---|---|---|---|---|
| 1 | **Automation platform**, end to end without manual steps | OK for Agent 2 / TEAM for the whole chain | `n8n/workflows/agent2_diagnosis.json` (webhook + "called by main workflow" trigger), tested through HTTP (`tests/reliability.py`); the Streamlit page calls it the same way | The main Telegram workflow (C2) is not in this copy of the repo: the end-to-end Telegram run must be tested by the team |
| 2 | **Prompt engineering**: versioned library, role, technique, failure cases, fixes, a before/after per key prompt | OK | `prompts/P3`, `P4`, `P5`: header (role, model, settings, technique), version tables, **"Failure cases and before / after"** with measured numbers (P5: 1/8 -> 7/8; P4: 3/6 -> 12/12) | P1, P2, P6, P7 belong to other owners. Versions of P3 and P5 v2.4 were measured on one case only |
| 3 | **RAG**: own KB, justified corpus, chunking (size, overlap), embeddings + vector store, retrieval setup, short evaluation, two chunkings compared | PARTIAL | Corpus: 6 official owner's manuals (4 brands) + 7,981 OBD-II codes (OBDex, CC0). Chunking A (500 words, 50 overlap) vs B (by heading, max 350 words) **compared on 30 queries** (`tests/eval_results.md`): Recall@5 0.72 vs 0.83. Embeddings BGE-M3. Hybrid dense + BM25 vs dense measured. Filters make / model / year / section, top-k 5. Evaluation set `rag/eval/queries.jsonl` | **Vector store**: the numbers come from my numpy stand-in (`dev_rag`); the team's Qdrant service (B1) is not built, so the vector store choice cannot be defended with a measured system yet. **Reranker**: none (matrix row missing). Corpus is 6 documents, the brief suggests 20-40 |
| 4 | **Multi-agent**, justified architecture, tools, information flow, why not an alternative | PARTIAL / TEAM | Agent 2 = LLM + `rag_search` tool + guardrails + safety net, contracts in `contracts/`, flow in `ARCHITECTURE.md`; justification of Agent 2's design in `report/agent2_sections.md` with measurements (tool loop, prefetch alternative) | The cross-agent justification (why not one prompt / a router / a debate) is the team's section; the three agents have not been run together |
| 5 | **Multimodality** (audio, image, speech...) | TEAM | Agent 1 turns audio into a structured result; Agent 2 reads it through P3 (v2, percentile score read correctly, reading guide) | Agent 1 is still in mock mode in the integration; TTS reply (Piper) is not Agent 2's |

## B. Deliverables

| Deliverable | Status | Where |
|---|---|---|
| Exported workflow or repository with run instructions | PARTIAL | `n8n/workflows/agent2_diagnosis.json` (team default: Gemini) + `agent2_diagnosis/README.md` (how to run, Ollama variant). **Nothing is committed**: this folder has no `.git`; branch + PR still to do |
| Technical report, 8 pages max, covering the five points | OPEN / TEAM | Draft of B2's sections: `report/agent2_sections.md` (LLM choice, Agent 2 architecture, retrieval, evaluation, what did not work) |
| Prompt library as a separate file | OK | `prompts/` (one file per prompt, index in `prompts/README.md`) |
| Short retrieval evaluation (queries, results, comments) | OK | `rag/eval/queries.jsonl`, `agent2_diagnosis/tests/eval_results.md`, `tests/eval_matrix.jsonl` |
| Live demo 15 min + 10 min questions | TEAM | Scenarios that work today: Clio starter / warning lights / overheating / tyre pressure (see `tests/cases/README.md`) |

## C. Grading criteria (weights of the brief)

| Criterion | Weight | Where we stand for Agent 2 |
|---|---|---|
| Prompt engineering: quality, iteration, documentation | 20 % | Strong: three prompts, 13 version rows, measured before/after, honest open items. Would gain from more cases per prompt |
| RAG: corpus, chunking, retrieval, evaluation | 25 % | Good evidence (two chunkings, hybrid vs dense, section filter, no-answer analysis). Missing: reranker, the real vector store, a larger corpus |
| Multi-agent architecture and its justification | 25 % | Agent 2 part documented and measured; team part to finish |
| Multimodality integration | 15 % | Depends on Agent 1 real model + TTS |
| Demo, report, Q&A | 15 % | Report and demo are team work; Q&A: every member must explain Agent 2, `report/agent2_sections.md` is written for that |

The brief also says: *justification matters more than complexity* and *reporting what did not work earns credit*. Both are covered in `report/agent2_sections.md` (section "What did not work").

## D. Rules of the team repo

| Rule | Status |
|---|---|
| Contracts unchanged, outputs valid against `agent2_output.schema.json` | OK (no contract edited; R1 schema check runs on every answer) |
| Citations must be chunks really retrieved (R2) | OK, but **existence is not support**: see "Known weaknesses" |
| Safety (R3): brakes / steering / overheating | OK with a documented deviation: `safety_stop` follows the most likely cause only (user decision) |
| Prompts live in `prompts/` with version history, never only in n8n | OK (the workflow is generated from the files) |
| No secrets or raw data in git | OK: CI-style scan clean, `.env`, indexes and `n8n_data` are git-ignored |
| CI runs the tests | OK locally: `agent2_diagnosis/tests` and the Node test added to `.github/workflows/ci.yml` (25 tests pass with the CI command) |
| One branch per task, pull request, one reviewer | OPEN (no `.git` in this copy) |
| `tests/cases/`: input case + expected behaviour | OK (`tests/cases/README.md`) |
| `agent2_diagnosis/README.md`: sections `symptoms, causes, codes` | DEVIATION applied on `agent2/reranker`: `maintenance` added (+7.7 points of hit rate without the reranker); the team must confirm (Agent 3 also reads that section) |

## E. Known weaknesses (say them, do not hide them)
1. **A citation that exists is not a citation that supports.** On a question the manuals do not cover (belt squeal) the 7B model cites an unrelated passage and the guardrail accepts it. Partly fixed: a support check (bge-reranker-v2-m3, `supported_causes` / `supportFilter`) drops a cause whose cited passages do not come back when its own name is searched. Belt squeal no longer cites the starter table. Through n8n (Ollama 7B, reranker on) the workflow now answers `insufficient` (the reranked searches return nothing; the agent errors out, the workflow treats it as no answer); starter pass 1 and 2 still give valid answers (108 s and 138 s on CPU reranking). In the Python path `diagnose` still keeps "belt slippage" through OBDex belt-driven generator codes: the check works on the cause name, not the symptom.
2. **No-answer is not rejected by retrieval**: 1 of 4 with the dense score alone (3 of 4 with the reranker at threshold 0.03, for a hit rate of 0.808 against 0.846; see `tests/eval_results.md`); dense scores overlap (answers 0.49-0.80, on-topic no-answer 0.57-0.62).
3. **Answer quality of the local 7B model** is limited (duplicate labels, checks that need tools, sometimes English). Gemini Flash is stronger but the free tier gives about 5 runs a day; billing was refused.
4. The evaluation set is small (30), written from the same manuals by the team, and labelled by one reader.
5. The vector store and the production reranker of the architecture (Qdrant, bge-reranker) are not built: the dev RAG runs bge-reranker-v2-m3 on CPU (about 4 s per search).
