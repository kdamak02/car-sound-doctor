# Retrieval evaluation (owner B2) — 2026-10-15

Service under test: the dev `/search` stand-in (`agent2_diagnosis/dev_rag`): BGE-M3 dense search, optionally fused with BM25
(reciprocal rank fusion), over 6 owner's manuals (7,888 heading chunks / 1,417 fixed windows) + 7,981 OBD-II codes. No reranker.
Queries: `rag/eval/queries.jsonl`, 30 queries made by `tests/make_eval_queries.py` (16 direct, 6 reworded, 2 ambiguous,
2 unknown car, 4 no answer). Run: `tests/eval_dev_rag.py`. Raw numbers: `tests/eval_matrix.jsonl`.

## How the labels were made
Each expected passage was found by **reading the manual text** (keyword search in the chunk index, then reading the chunk), never
with the retriever. A passage counts as correct when its (manual, page) is one of the expected pages (for the fixed windows, a
window that starts one page earlier also counts). 26 queries have an answer in the corpus, 4 do not.

## Results (26 answerable queries, top 5)
| Chunking | Search | Sections searched | Recall@5 | MRR | Hit rate |
|---|---|---|---|---|---|
| A fixed 500 words / 50 overlap | dense | all | 0.676 | 0.520 | 0.769 |
| A fixed | hybrid | all | 0.715 | 0.696 | 0.808 |
| B by heading | dense | all | 0.740 | 0.604 | 0.808 |
| **B by heading** | **hybrid** | all | **0.830** | 0.667 | **0.885** |
| B by heading | dense | symptoms, causes, codes (Agent 2 today) | 0.801 | 0.629 | 0.846 |
| B by heading | hybrid | symptoms, causes, codes (Agent 2 today) | 0.801 | 0.688 | 0.846 |
| B by heading | hybrid | + maintenance | **0.869** | **0.732** | **0.923** |
| B by heading | hybrid | + maintenance, procedure, safety | 0.869 | 0.732 | 0.923 |

The section filter is meaningless for the fixed windows (they are not section-tagged: almost everything is `general`), so the
chunking comparison uses "all sections". The fixed rows with the Agent 2 filter (0.51 / 0.55) are therefore not a fair test.

## What it says
1. **Chunking by heading beats fixed windows**: Recall@5 0.83 vs 0.72 and hit rate 0.89 vs 0.81 (hybrid); 0.74 vs 0.68 and 0.81 vs 0.77 (dense). Fixed windows have a slightly higher MRR in hybrid (0.70 vs 0.67): the first correct window ranks well, but fewer relevant pages are found.
2. **Hybrid (dense + BM25) beats dense** on heading chunks: Recall@5 0.83 vs 0.74, MRR 0.67 vs 0.60, hit rate 0.89 vs 0.81. Exact words ("sous-gonflage", "AdBlue", "bouillonnement") matter in manuals.
3. **Agent 2's section filter (symptoms, causes, codes) costs retrieval quality**: hit rate 0.885 → 0.846 against no filter. Adding `maintenance` brings it to **0.923 (Recall@5 0.869)**, better than no filter, because it removes noise and keeps the procedures (jump start, fuel exhaustion). Adding `procedure` and `safety` changes nothing. **Proposal for the team: Agent 2 searches symptoms, causes, codes, maintenance** (the README of `agent2_diagnosis/` says the first three).
4. **No answer is not solved at retrieval level**: only 1 of 4 no-answer queries returns nothing (the pizza one). The three on-topic ones (belt squeal, timing-belt replacement, wheel-bearing noise) still return passages, with top scores 0.57-0.62, while real answers score from 0.49 (median 0.67): **no threshold on the score separates them**. It needs a reranker (bge-reranker, planned by B1) or the LLM stage; end to end, the agent's citation guardrail already answers "insufficient information" for the belt-squeal case.

## Misses (Agent 2 filter, hybrid)
- q05 "témoin de pression d'huile s'allume en virage": the right row is inside a long chunk of the Clio troubleshooting table (p.226, several rows merged); other pages about warning lights rank first. PDF tables are extracted as text blocks, so the brief's "one chunk per table row" is not reached. Next: split the 77 cause tables by row.
- q11 "démarrer avec la batterie d'un autre véhicule": the page is tagged `maintenance`, excluded by the current filter (fixed by proposal 3).
- q14 "voyant des pneus dégonflés ... bip" (reworded): the manual says "sous-gonflage"; dense + BM25 miss the colloquial wording.
- q23 (ambiguous, "un voyant orange"): only 2 such queries, read the hit rate, not the recall.

## Limits (say them in the report)
- 30 queries, one run (the search is deterministic). Per-type numbers rest on 2 to 16 queries.
- The queries and the labels were written from the same manuals by the team, so the wording overlaps the passages: results are optimistic compared with real drivers. Real Telegram messages (and the NHTSA complaints, in English) would be a harder test.
- No reranker, so no "with / without reranker" row yet. The service is a stand-in: B1's Qdrant version must be re-measured with the same queries (chunk ids differ, the (manual, page) labels do not).
- Labels are one person's reading: a teammate should check a sample of 10.

## With / without the cross-encoder reranker (bge-reranker-v2-m3, CPU, top-8 pool, 800 characters per passage)

Structure chunking, hybrid, sections symptoms+causes+codes, 30 queries. Reranker = drop passages whose score is under the threshold (hybrid order kept).

| | hit rate (answerable, 26) | Recall@5 | MRR | no-answer rejected (4) |
|---|---|---|---|---|
| no reranker | 0.846 | 0.801 | 0.688 | 1/4 |
| reranker, threshold 0.03 (default) | 0.808 | 0.763 | 0.702 | 3/4 |
| reranker, threshold 0.10 | 0.692 | 0.660 | 0.615 | 4/4 |

Reading: the reranker is the only signal that separates "no answer" queries (the dense score does not), at the price of some recall; the threshold is a trade-off chosen on 30 queries, so treat it as indicative. Reordering by reranker score (`DEV_RAG_RERANK_ORDER=true`) gave MRR 0.654 vs 0.615 at 0.10 but the same hit rate. CPU cost: about 4 s per search level.

Support check in Agent 2 (`agent2.supported_causes`): the belt-squeal case no longer cites the starter table but still keeps "belt slippage" through OBDex belt-driven starter/generator codes (the reranker rates them relevant to the cause name). The check works on the cause name, not on the symptom, so it does not close the gap alone; the real fix is a belt-squeal manual passage in the corpus.

## Section `maintenance` applied (Agent 2 now asks for symptoms, causes, codes, maintenance)
Structure chunking, hybrid, 30 queries: no reranker hit 0.923 / Recall@5 0.869 / MRR 0.732 / no-answer 1/4; with the reranker (threshold 0.03) hit 0.808 / 0.753 / 0.715 / 3/4. The reranker drops the same answerable queries as before, so the `maintenance` gain disappears when it is on.
