# Requirements check for Agent 2 (B2) and its integration — updated 2026-10-18

Sources: the course brief (`Advanced DL-Exam.pdf`: 5 mandatory requirements, deliverables, grading) and the team rules of this repo
(README, CONTRIBUTING, `agent2_diagnosis/README.md`, `report/README.md`). Status: OK = met and evidenced, PARTIAL = met with a limit,
OPEN = not done, TEAM = depends on other groups.

## A. The five mandatory requirements of the brief

| # | Requirement | Status | Evidence | Gap / what is left |
|---|---|---|---|---|
| 1 | **Automation platform**, end to end without manual steps | OK in tests, PARTIAL in practice | `n8n/workflows/full_chain_*.json` (webhooks for a JSON case and for a voice note + case: Agent 1 -> Agent 2 -> Agent 3), `n8n/workflows/telegram_front.json` (polling Telegram bot), `agent2_diagnosis/start_full_chain.ps1`; a full conversation was run through a **fake** Telegram with the real agents | Not tested with a real bot; the 30-minute timeout when the driver never answers is not implemented |
| 2 | **Prompt engineering**: versioned library, role, technique, failure cases, fixes, a before/after per key prompt | OK | `prompts/P3`, `P4`, `P5`: header (role, model, settings, technique), version tables, **"Failure cases and before / after"** with measured numbers (P5: 1/8 -> 7/8; P4: 3/6 -> 12/12) | P1, P2, P6, P7 belong to other owners. Versions of P3 and P5 v2.4 were measured on one case only |
| 3 | **RAG**: own KB, justified corpus, chunking (size, overlap), embeddings + vector store, retrieval setup, short evaluation, two chunkings compared | PARTIAL | Corpus: 6 official owner's manuals (4 brands) + 7,981 OBD-II codes (OBDex, CC0). Chunking A (500 words, 50 overlap) vs B (by heading, max 350 words) **compared on 30 queries** (`tests/eval_results.md`): Recall@5 0.72 vs 0.83. Embeddings BGE-M3. Hybrid dense + BM25 vs dense measured. Filters make / model / year / section, top-k 5. Evaluation set `rag/eval/queries.jsonl` | **Vector store**: the numbers come from my numpy stand-in (`dev_rag`); the team's Qdrant service (B1) is not built, so the vector store choice cannot be defended with a measured system yet. **Reranker**: none (matrix row missing). Corpus is 6 documents, the brief suggests 20-40 |
| 4 | **Multi-agent**, justified architecture, tools, information flow, why not an alternative | OK | Three agents with fixed JSON contracts (`contracts/`), one n8n AI Agent node with a RAG tool per LLM agent, different models per agent (Agent 2 Ollama/Groq/Gemini, Agent 3 Groq Qwen, Agent 1 a trained AST model, not an LLM); hand-over Agent 2 -> 3 (`agent2_diagnosis/to_agent3.py`, validated against Agent 3's schema); alternatives measured in `report/agent2_sections.md` section 6 | Agent 3's guides are generic for our demo faults (note to group C) |
| 5 | **Multimodality** (audio, image, speech...) | OK | Voice note (OGG/Opus) -> Agent 1 (real model, tested: ok / `unusable_audio` / empty file) -> `case.audio` -> prompt P3 (v2, reading guide) -> Agent 2; text and voice both used in the Telegram front | Tested with a synthetic clip only; the real demo recording from group A is still to run; no TTS |

## B. Deliverables

| Deliverable | Status | Where |
|---|---|---|
| Exported workflow or repository with run instructions | OK | `n8n/workflows/*.json`, `agent2_diagnosis/HANDOVER.md`, `agent2_diagnosis/README.md`, `agent2_diagnosis/start_full_chain.ps1`, `n8n/telegram_front/README.md`; all pushed on `integration/agent2-agent3` (PR 2 `agent2/reranker` for Agent 2 only) |
| Technical report, 8 pages max, covering the five points | PARTIAL / TEAM | B2's sections are written: `report/agent2_sections.md` (LLM choice, Agent 2 architecture and integration, retrieval evaluation, what did not work); the team report still has to be assembled and cut to 8 pages |
| Prompt library as a separate file | OK | `prompts/` (one file per prompt, index in `prompts/README.md`) |
| Short retrieval evaluation (queries, results, comments) | OK | `rag/eval/queries.jsonl`, `agent2_diagnosis/tests/eval_results.md`, `tests/eval_matrix.jsonl` |
| Live demo 15 min + 10 min questions | PARTIAL | `agent2_diagnosis/DEMO_DAY.md` (checklist, 6-minute script, fallbacks, questions), `agent2_diagnosis/tests/preflight.py`; needs the real bot, the recording from group A and a backup video |

## C. Grading criteria (weights of the brief)

| Criterion | Weight | Where we stand for Agent 2 |
|---|---|---|
| Prompt engineering: quality, iteration, documentation | 20 % | Strong: three prompts (P4/P5 up to v2.5), measured before/after, honest open items. Would gain from more cases per prompt |
| RAG: corpus, chunking, retrieval, evaluation | 25 % | Good evidence (two chunkings, hybrid vs dense, section filter, reranker with and without, no-answer analysis: 4/4 rejected with it). Missing: the shared vector store (Qdrant) for Agent 2, a larger corpus and evaluation set |
| Multi-agent architecture and its justification | 25 % | Documented and measured for Agent 2, integrated and tested end to end with Agents 1 and 3 |
| Multimodality integration | 15 % | Agent 1 real model integrated and tested with a synthetic clip; real recording and TTS still open |
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
| CI runs the tests | OK | `agent2_diagnosis/tests` (24 tests), the Node tests of the n8n code nodes and of the Telegram logic are in `.github/workflows/ci.yml` |
| One branch per task, pull request, one reviewer | PARTIAL | PR 1 `agent2/diagnosis-logic` and PR 2 `agent2/reranker` opened; the integration branch `integration/agent2-agent3` is pushed to by several people (no PR yet: it carries about 90 MB of Agent 3 data) |
| `tests/cases/`: input case + expected behaviour | OK (`tests/cases/README.md`) |
| `agent2_diagnosis/README.md`: sections `symptoms, causes, codes` | DEVIATION applied on `agent2/reranker`: `maintenance` added (+7.7 points of hit rate without the reranker); the team must confirm (Agent 3 also reads that section) |

## E. Known weaknesses (say them, do not hide them)
1. **A citation that exists is not a citation that supports.** For a symptom the manuals do not cover, a small model can cite an unrelated passage and the guardrail accepts it. Partly fixed: a support check (bge-reranker-v2-m3, `supported_causes` / `supportFilter`) drops a cause whose cited passages do not come back when the cause name and the symptom are searched. Through n8n (Ollama 7B, reranker on) such a case now ends as `insufficient`; covered cases (starter, oil pressure, power steering, charge light) still give cited answers. Limit: in the Python path an unrelated OBDex code can still pass for a cause it merely mentions.
2. **No-answer rejection costs recall**: 1 of 4 with the dense score alone; with the reranker 4 of 4 but the hit rate goes 0.923 -> 0.846 (`tests/eval_results.md`); tuned on 30 queries only.
3. **Answer quality of the local 7B model** is limited (duplicate labels, checks that need tools, sometimes English). Gemini Flash is stronger but the free tier gives about 5 runs a day; billing was refused.
4. The evaluation set is small (30), written from the same manuals by the team, and labelled by one reader.
5. The vector store of the architecture (Qdrant) is not used by Agent 2: the dev RAG is a numpy stand-in with the same `/search` contract; the reranker runs on the CPU (slow) or on the GPU (then the local LLM does not fit).

6. **Agent 3's guides are generic** for our faults and its validator accepts them (`NOTE_FOR_AGENT3.md`).
7. **Groq free tier**: 200,000 tokens per day per model for the whole team; one run needs about 7,000 (`HANDOVER.md`, `preflight.py`).
8. **Not tested**: a real Telegram bot, a real engine recording, several users at once, the 30-minute answer timeout.