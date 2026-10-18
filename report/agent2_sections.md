# Report sections written by B2 (Agent 2 and its integration) — figures measured 2026-10-15 to 2026-10-18

Use these as the base of sections 3, 6 (Agent 2 part), 7 (retrieval) and Annex A of `report/README.md`. Every number comes from a file of the repo
(named in brackets) and can be re-run. Limits are written next to the numbers on purpose.

## 3. Choice of the LLM (quality, cost, latency, context, licence)

Agent 2 needs: JSON output, tool calling (the `rag_search` HTTP tool), French, answers grounded in passages. Three providers run behind the same workflow (`LLM=gemini|groq|ollama` in `agent2_diagnosis/build_workflow.py`).

| | Gemini Flash (team default) | Groq `gpt-oss-120b` (shared team key) | Ollama `qwen2.5:7b` (local) |
|---|---|---|---|
| Quality on our cases | valid grounded answers on the cases tried; few runs possible (quota) | **8 of 8** usable runs on the four cases the manuals cover (2 runs each); the power-steering case 0 of 2 | pass 1: 4/4 usable, pass 2: 6/6 with a rescue step; weaker reasoning: ignores contradicting answers, duplicate labels, occasional English |
| Latency (n8n, one pass) | 15-40 s, 503 overloads on the free tier | 6-54 s with the reranker on the GPU (40-150 s on the CPU) | 13-69 s; the whole conversation (2 passes) 1.5-3 min |
| Cost / limits | free tier: **20 requests per model per day**; billing refused | free tier: **8,000 tokens per minute and 200,000 per day per model for the whole organization**; one run needs about 7,000 tokens, so about 28 runs a day; our testing used the 120b budget up | 0 (own GPU); electricity only |
| Context window | very large | 128 K class | 8,192 tokens configured (model supports 32 K); a run needs about 5 K |
| Licence / data | proprietary API; free-tier prompts may be used to improve the provider's products | open weights served by a US provider; prompts leave the PC | open weights (check the model card, expected Apache-2.0); **data stays on the PC** |

Also measured: Groq `gpt-oss-20b` (separate budget) answered 4 of 8 on the covered cases, so it is only a fallback.
Choice: Groq `gpt-oss-120b` for the demo when its budget allows (best quality and speed), Ollama `qwen2.5:7b` as the no-quota fallback and for development, Gemini as the team default workflow. Claude is supported by the code (`agent2.ask`) but was not measured (no key).
Limits: 2-6 runs per number, one machine, no cost in euros because no paid tier was used. The daily token budget is a real operational constraint (see section 8).

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

**Advice, not only checks (P4/P5 v2.5).** `message_to_user` now gives ordered advice: one concrete action for the most likely cause with the manual page, one short line per less likely cause, a caution when a less likely cause involves brakes, steering, overheating or oil pressure, and no repair steps (that is Agent 3's job).

**Reranker and support check.** The search service reranks the best 8 candidates with `bge-reranker-v2-m3` (a cross-encoder; the passage text is cut at 1,500 characters) and drops passages under a score (0.03 for the exact vehicle, 0.10 for relaxed vehicle levels). Then `supported_causes` (Python) / `supportFilter` (n8n) searches each of the top 3 causes with its name plus the symptom and keeps the cause only if one of its cited passages comes back: a citation that exists is not necessarily a citation that supports.

**Guardrails in code, not in the prompt** (a small model does not follow them reliably): only retrieved chunk ids can be cited (R2); `conclusive` = top cause >= 0.6 and 0.2 ahead; `safety_stop` follows the most likely cause;
unknown labels become null; a cause the driver contradicts is capped at 0.05 (P5 v2.4: 1/8 -> 7/8); round cap 2.

**Hand-over and integration.** Agent 1 (audio, FastAPI + AST model, not an LLM) is called by n8n (`POST /analyze`, voice note as is + the context); its answer becomes `case.audio`, which prompt P3 turns into text with a reading guide (0.80 / 0.95 unusualness rules). If the recording is unusable the driver is asked for a new one; if Agent 1 is down Agent 2 works from the text only (tested). When Agent 2 is conclusive or has no checks left, `to_agent3` maps the case and the last output to Agent 3's input schema and Agent 3 returns a validated, cited guide (tested with Agent 3's own schema). A Telegram front (polling bot: voice note, car, symptom, context buttons, one check at a time with buttons) drives the whole chain; it was tested against a fake Telegram server with the real agents (a conversation takes 1.5-3 minutes), **not yet with a real bot**.

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
With and without the reranker (final settings, 4 sections, structure chunking, hybrid):

| | hit rate | Recall@5 | MRR | no-answer rejected (4) |
|---|---|---|---|---|
| no reranker | 0.923 | 0.869 | 0.732 | 1/4 |
| reranker (0.03 exact vehicle, 0.10 relaxed levels) | 0.846 | 0.792 | 0.753 | **4/4** |

Trade-off: the reranker is the only signal that separates "no answer" from a weak answer, at the cost of 8 points of hit rate. Tuned on these 30 queries only.
Limits: 30 queries, one labeller, queries share the vocabulary of the manuals, results are optimistic.

## 6 bis / 8. What did not work (and why it is in the report)
1. A 7B model as a tool-calling agent, without help: 0 usable runs in about 25. Cause: model behaviour + full GPU memory, found with a replica of the loop.
2. n8n output parsers (structured, auto-fixing): rejected every answer.
3. Free-text prompt rule for contradicting answers: 3/8 (from 1/8). A status field + code cap: 7/8.
4. Fixed-size chunks: they cut the troubleshooting tables.
5. Retrieval score as a "no answer" detector: the scores overlap.
6. Citation guardrail that checks existence only: on a symptom the manuals do not cover, the model can cite an unrelated passage. Partly fixed: a support check with the reranker drops causes whose citations do not come back when the cause and the symptom are searched; through n8n such a case ends as `insufficient`. Limit: an unrelated OBDex code can still pass in the Python path.
7. Gemini free tier: 20 requests per model per day, shared by the Flash models; a development day exhausted it.
8. Groq free tier: 8,000 tokens per minute and 200,000 per day per model for the whole team. Our back-to-back test batches used up `gpt-oss-120b` and every run then failed after about 100 s of retries; found by reading the error of the n8n execution, not by guessing. Lesson: no test batches on the demo day, check the budget first (`agent2_diagnosis/tests/preflight.py`), keep the Ollama fallback.
9. The reranker on the CPU: 40-150 s per run; on the GPU 6-54 s, but then the GPU cannot also host the local LLM.
10. A reranker threshold that was too strict dropped correct passages (hit rate 0.846 -> 0.692 at 0.10 for every level); the final settings use a lower threshold for the exact vehicle and a stricter one for relaxed levels.
11. The power-steering case fails on every model tried (the relevant Clio passage scores low with the reranker and small models cite label names instead of passage ids): kept as a known limit.
12. The repair guides of Agent 3 are generic for our demo faults (a "disconnect the battery" guide for a starter case) although they pass its validator; reported to group C with evidence (`agent2_diagnosis/NOTE_FOR_AGENT3.md`).
14. An output cap of 1,500 tokens on the Groq node truncated the JSON answer of the reasoning model (its hidden reasoning counts against the cap): pass 2 failed with "the model cited none" until the cap was raised to 4,000; found by reading the raw agent output of the n8n execution.
15. The support check re-searched with the reranker and vetoed a correct answer (the long Clio troubleshooting table scored low for the verification query); it now searches without the reranker threshold and only requires the cited page to come back in the top 8.
13. A Python client sent the voice note without a content type and n8n did not see a file; curl added it automatically. Found with the n8n execution data.

## Annex A — prompt library
`prompts/P3_audio_description.md`, `P4_checks_generation.md`, `P5_reranking.md`: role, technique, settings, version table (P4 and P5 up to v2.5), failure cases with before / after and measured effect.
