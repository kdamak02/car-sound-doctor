# Report sections written by B2 (draft, Agent 2) — figures measured on 2026-10-15

Use these as the base of sections 3, 6 (Agent 2 part), 7 (retrieval) and Annex A of `report/README.md`. Every number comes from a file of the repo
(named in brackets) and can be re-run. Limits are written next to the numbers on purpose.

## 3. Choice of the LLM (quality, cost, latency, context, licence)

Agent 2 needs: JSON output, tool calling (the `rag_search` HTTP tool), French, answers grounded in passages.

| | Gemini Flash (team default) | Ollama `qwen2.5:7b` (local, used for development) |
|---|---|---|
| Quality on our cases | valid grounded answers on the cases tried; few runs possible (quota) | pass 1: 4/4 usable, pass 2: 6/6 usable (Clio starter case). Weaker reasoning: ignores contradicting answers unless forced (prompt P5 v2.4), duplicate labels, checks that need tools |
| Latency | 15-40 s per run, 503 overloads on the free tier | 13 s (pass 1), 16-34 s (pass 2) on an RTX 4060 8 GB |
| Cost | free tier: **20 requests per model per day**, one run uses 3-4, so about 5 runs a day; billing refused | 0 (own GPU); electricity only |
| Context window | very large | 8,192 tokens configured (model supports 32 K); a run needs about 5 K |
| Licence / data | proprietary API; free-tier prompts may be used to improve the provider's products | open weights (check the model card, expected Apache-2.0); **data stays on the PC** |

Choice: Gemini Flash as the production target, Ollama `qwen2.5:7b` for development, measurements and the demo when the quota is gone. The code supports
Gemini, Claude and Ollama behind one function (`agent2.ask`, `AGENT2_PROVIDER`); Claude was not measured (no key).
Limits: 4-6 runs per number, one machine, no cost in euros because no paid tier was used.

## 6. Agent 2 in the architecture

**Role.** From the case record (vehicle, symptom, Agent 1's audio result, the driver's answers) it ranks 2-5 causes and asks 2-5 checks the driver can do without tools, each
with a citation of a retrieved passage; in pass 2 it re-ranks with the answers. **Tools.** `rag_search` = `POST /search` (filters make, model, year, section). **Output.**
`agent2_output.schema.json`; the next agent (repair guide) reads `causes`, `conclusive`, `safety_stop`.
**Flow.** `Prepare input` (P3 audio text, P4 or P5) -> AI Agent node + `rag_search` -> `Ground and decide` (code) -> `Guardrails R2/R3` (team node) -> output or "insufficient information".

**Why this design (and what we measured against the alternatives)**

| Alternative | Result | Decision |
|---|---|---|
| One big prompt, no retrieval | no citations, no way to check the answer against the manual | rejected on principle: the guardrail needs retrieved chunks |
| Retrieve first in a Code node, one LLM call in JSON mode ("prefetch") | **18/18** usable with the 7B model, 30 s, but the model chooses nothing | kept as a **fallback** (`MODE=prefetch`): it is a RAG chain, not an agent |
| AI Agent node with the tool, model alone | with the 7B model: **0 of about 25** usable runs in n8n (empty final message, endless tool calls, prose instead of JSON) | fixed, see below |
| Same + parsers (Structured Output Parser, Auto-fixing) | "Model output doesn't fit required format" in all runs, even with a very loose schema | rejected |
| **AI Agent node + explicit working procedure in the prompt + temperature 0 + safety net** | pass 1: 4/4 and 6/6, pass 2: 6/6 (13-34 s) | **chosen** |

What made the agent loop work with a small model (offline replica of the n8n loop, `tests/tool_loop_probe.py`: 3/6 -> 12/12 usable): (1) free the GPU (the embedding model of the search service moved to the CPU:
generation had fallen from 48 to 8 tokens/s because the 8 GB card was full), (2) a "How to work" block at the end of P4/P5 (one search, then JSON only), (3) temperature 0, (4) a safety net: if the agent returns
an empty answer, one JSON-mode call answers from the passages the agent already retrieved (pass 2: 0/4 -> 6/6).

**Guardrails in code, not in the prompt** (a small model does not follow them reliably): only retrieved chunk ids can be cited (R2); `conclusive` = top cause >= 0.6 and 0.2 ahead; `safety_stop` follows the most likely cause;
unknown labels become null; a cause the driver contradicts is capped at 0.05 (P5 v2.4: 1/8 -> 7/8); round cap 2.

## 7. Retrieval evaluation (B2) — `agent2_diagnosis/tests/eval_results.md`

Set: 30 queries (16 direct, 6 reworded, 2 ambiguous, 2 unknown car, 4 with no answer) on 6 owner's manuals (Renault Clio 4, Peugeot 208 x2, Ford Fiesta, Ford Focus, Hyundai i10) + 7,981 OBD-II codes.
Labels were made by reading the manuals (not with the retriever); a hit is a passage of the expected (manual, page).

| Chunking | Search | Sections | Recall@5 | MRR | Hit rate |
|---|---|---|---|---|---|
| A fixed 500 words, 50 overlap | dense | all | 0.68 | 0.52 | 0.77 |
| A fixed | hybrid | all | 0.72 | 0.70 | 0.81 |
| B by heading (max 350 words) | dense | all | 0.74 | 0.60 | 0.81 |
| B by heading | hybrid | all | 0.83 | 0.67 | 0.89 |
| B by heading | hybrid | symptoms, causes, codes (current) | 0.80 | 0.69 | 0.85 |
| **B by heading** | **hybrid** | **+ maintenance** | **0.87** | **0.73** | **0.92** |

Findings: heading chunks beat fixed windows; adding BM25 (reciprocal rank fusion) beats dense alone; the section filter planned in the README loses 4 points of hit rate, adding `maintenance` gains 4 more than no filter at all;
**no-answer queries are not separated by the score** (3 of 4 still return passages with scores 0.57-0.62 against 0.49-0.80 for real answers): a reranker is needed. With bge-reranker-v2-m3 as a filter (threshold 0.03) 3 of 4 are rejected and hit rate goes 0.846 -> 0.808 (threshold 0.10: 4 of 4, 0.692): a recall/rejection trade-off measured on 30 queries. Not done: the real vector store (Qdrant, B1), row-level chunking of the cause tables.
Limits: 30 queries, one labeller, queries share the vocabulary of the manuals, results are optimistic.

## 6 bis / 8. What did not work (and why it is in the report)
1. A 7B model as a tool-calling agent, without help: 0 usable runs in about 25. Cause: model behaviour + full GPU memory, found with a replica of the loop.
2. n8n output parsers (structured, auto-fixing): rejected every answer.
3. Free-text prompt rule for contradicting answers: 3/8 (from 1/8). A status field + code cap: 7/8.
4. Fixed-size chunks: they cut the troubleshooting tables.
5. Retrieval score as a "no answer" detector: the scores overlap.
6. Citation guardrail that checks existence only: on a symptom the manuals do not cover, the model can cite an unrelated passage. Partly fixed: a support check with the reranker drops causes whose citations do not come back when the cause and the symptom are searched; through n8n such a case ends as `insufficient`. Limit: an unrelated OBDex code can still pass in the Python path.
7. Gemini free tier: 20 requests per model per day, shared by the Flash models; a development day exhausted it.

## Annex A — prompt library
`prompts/P3_audio_description.md`, `P4_checks_generation.md`, `P5_reranking.md`: role, technique, settings, version table (13 rows), failure cases with before / after and measured effect.
