# System evaluation

Three levels, each with its own owner and section in the report.

| Level | What | Where | Owner |
|---|---|---|---|
| Audio model | AUC, F1 per class, confusion matrix, test on real Telegram clips | `agent1_audio/notebooks/` | A2, A3 |
| Retrieval | Recall@5, MRR, hit rate, no-answer rejection; chunking A/B × dense/hybrid × rerank | `rag/eval/` | B2 |
| End to end | scenarios below, run through Telegram | `evaluation/scenarios.csv` | everyone |

## End-to-end scenarios

`scenarios.csv`: one row per test conversation. Columns:
`id, audio_file, user_text, expected_fault, expected_safety_stop, got_fault, correct, citations_ok,
rounds, latency_s, cost_usd, notes`.

Cover at least: clear known fault, ambiguous sound, unknown car model, user who never answers
(timeout), unusable audio (speech only), safety fault (brakes), prompt injection in the user text.

Measure and report: % correct diagnosis, % answers with valid citations, % correct refusals,
average latency, average cost per case.
