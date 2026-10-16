# P4 — Agent 2 pass 1, causes and checks

| | |
|---|---|
| Owner | B2 |
| Used in | Agent 2 pass 1 (n8n AI Agent node / `agent2_diagnosis/agent2.py`) |
| Model | Gemini Flash (team default, free tier: 20 requests/day) or local Ollama `qwen2.5:7b` (used for development and the measurements below) |
| Settings | temperature 0 (Ollama in n8n; 0.2 for Gemini), max tokens 1200-1500, JSON output |
| Technique | role + reasoning + citation required |
| Output contract | `contracts/agent2_output.schema.json` (the code converts this prompt's JSON into it) |

## Prompt (current version)

```text
You are a car diagnostic assistant helping a driver who is not a mechanic.
You receive: the vehicle, the driver's description and an automatic analysis of the engine sound.
Manual passages come from the rag_search tool when you have it (call it once), or are listed in the message with their chunk_id in square brackets,
for example [peugeot208_fr_p142_c3]. Never use knowledge that is not in a passage.

Task: rank the 2 to 5 most likely causes, then ask 3 to 5 checks the driver can do WITHOUT tools to tell
the causes apart.

Rules:
- Use ONLY the passages for facts. Every cause and every check must cite passage ids, copied exactly.
- The sound analysis is a hint, not a fact. If its confidence is low, say so in the evidence.
- A cause "label" must be one of the label ids given to you, or null.
- Each check: one short question answerable in one message; "answer_type" is yes_no, choice or free_text
  (give "choices" for choice). Never ask for a check that requires driving when brakes, steering or
  overheating are involved.
- Do not ask for something the driver already told you.
- If the passages do not support any cause, return {"causes": [], "checks": []}. Do not guess.
- If the retrieval level is not exact_model, say in message_to_user that the answer uses general
  information, not this exact model.
- Order causes from the most to the least likely. In message_to_user speak first about the most likely cause,
  then mention the less likely ones briefly. If a less likely cause involves brakes, steering, overheating or
  oil pressure, add one sentence telling the driver to be careful until it is ruled out.
- "name" is a short readable phrase in the driver's language, never a label id or a chunk id. Each "evidence" line is a
  short fact from a passage or a quote of the driver, not an id.
- The audio analysis ends with a "Reading guide" line: follow it (for example, when the sound is probably healthy, ask
  whether the noise is intermittent instead of assuming a fault).
- Write name, q, why and message_to_user in the driver's language.

Answer with JSON only:
{"causes": [{"name": str, "label": str|null, "prob": float,
             "evidence": [str], "sources": ["chunk_id"]}],
 "checks": [{"q": str, "why": str, "answer_type": "yes_no|choice|free_text",
             "choices": [str], "source": "chunk_id"}],
 "message_to_user": str}
Probabilities across causes sum to 1.

How to work when you have the rag_search tool:
1. Call it ONCE with a short query about the driver's problem.
2. When you receive the passages, STOP calling tools and write the final answer.
3. The final answer is ONLY the JSON object described above: no sentence before or after it, no markdown.
Copy every chunk_id exactly as it appears in the passages.
```

## Input variables

User message built by the code: vehicle, driver's description, language, P3 text, retrieval level, label ids, passages.

| Variable | Source |
|---|---|
| vehicle | case record `vehicle` |
| description, language | `symptom.description`, `user_input.language` |
| audio text | P3 |
| retrieval level | `fallback_level` of the last `/search` answers |
| passages | `/search` results, `section` in symptoms, causes, codes, maintenance |

## Test inputs

| Case | Input | Expected |
|---|---|---|
| mock case | `contracts/examples/case_record.json`, mock `/search` | first cause from the mock case, checks cite the mock chunk ids |
| knocking Kia Rio | real index, "knocking noise when I accelerate, oil light came on once" | low-oil cause among the causes; no cause cited only from a knock-sensor code |

## Versions

| Version | Date | Change | Why (observed failure) | Effect |
|---|---|---|---|---|
| v1 | 2026-10-16 | first version, ported from the standalone prototype; safety flag moved from the prompt to code | prototype: the LLM left risky causes unflagged | `safety_stop` is now decided in code from the cause name and label |
| v2 | 2026-10-16 | works with the rag_search tool (n8n AI Agent) or with passages in the message; most likely cause first, caution sentence for less likely risky causes | team decision: recommendations ordered most likely then less likely; Agent 2 is an n8n AI Agent node | to measure |
| v2.1 | 2026-10-16 | search once or twice (was 1 to 3 times) | local 7B model (Ollama): 3 searches overflowed the 8k context and the run timed out after 5 min | to measure |
| v2.2 | 2026-10-16 | rule: readable cause names, evidence as short facts | local 7B model answered with label ids ('dead_battery') as names and chunk ids as evidence | names readable; code also maps label ids to names |
| v2.3 | 2026-10-16 | rule: follow the audio "Reading guide" line (computed from Agent 1's decision table) | Agent 1's integration guide defines how to read label + anomaly score + confidence | to measure |
| v2.4 | 2026-10-16 | explicit working procedure when the rag_search tool exists (one search, then the final JSON only, ids copied exactly); temperature 0 in the n8n model node | local 7B model (qwen2.5:7b) as tool-calling agent: in an offline replica of the n8n agent loop 3 of 6 runs usable (empty final message after the tool result x2, endless tool calls x1), plus 0 of about 25 in n8n | 12 of 12 usable (offline replica), 6 of 6 at temperature 0, about 14 s |

## Failure cases and before / after

**1. The model used machine ids instead of words (v2.1 -> v2.2).** Local 7B model, Clio starter case.

| | Cause name | Evidence |
|---|---|---|
| Before | `dead_battery`, `bad_ignition` | `clio4ph1fr_p225_c1`, `obdex_P2503` (chunk ids) |
| After | `Batterie déchargée ou hors d’usage` | `Le démarreur ne tourne pas, j'entends seulement un clic, le voyant de batterie est allumé` (a quote) |

Fix: one prompt rule (readable names, evidence = a fact or a quote) plus a code guard that maps a label id used as a name to the label's name.

**2. The tool-calling loop broke (v2.3 -> v2.4).** Same model as a tool-calling agent (n8n AI Agent + `rag_search`).
An offline replica of the agent loop made the failure visible: of 6 runs only 3 were usable; 2 ended with an **empty final message after the tool result**, 1 never stopped calling the tool (6 calls). In n8n: 0 usable in about 25 runs.

| | Usable answers | Time |
|---|---|---|
| Before (v2.3, temperature 0.2) | 3/6 offline, 0/~25 in n8n | 5-120 s |
| After (v2.4: "How to work" block at the end of the prompt, temperature 0) | 12/12 offline, 4/4 and 6/6 in n8n | about 14 s |

Two other causes were not prompt problems and are documented in the report: the GPU memory was full (generation fell from 48 to 8 tokens/s) and n8n's Structured Output Parser / Auto-fixing parser did not help ("Model output doesn't fit required format").

**3. Open, not fixed: a citation that exists is not a citation that supports.** For a symptom the manuals do not cover, the 7B agent cited an unrelated table of the Clio manual. The guardrail only checks that the cited chunk was retrieved. Planned: a reranker / cross-encoder support check.

## Failure to watch

Generic checks that are not in the manual. Mechanical faults (bearings, low oil) supported only by electrical OBD-II codes (e.g. a knock sensor code cited for a knocking engine): needs real manuals in the index.
