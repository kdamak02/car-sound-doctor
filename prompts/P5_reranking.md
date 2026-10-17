# P5 — Agent 2 pass 2, re-ranking

| | |
|---|---|
| Owner | B2 |
| Used in | Agent 2 pass 2 and 3 (n8n AI Agent node / `agent2_diagnosis/agent2.py`) |
| Model | Gemini Flash (team default, free tier: 20 requests/day) or local Ollama `qwen2.5:7b` (used for development and the measurements below) |
| Settings | temperature 0 (Ollama in n8n; 0.2 for Gemini), max tokens 1200-1500, JSON output |
| Technique | structured reasoning over answers |
| Output contract | `contracts/agent2_output.schema.json` (converted by the code) |

## Prompt (current version)

```text
You are a car diagnostic assistant helping a driver who is not a mechanic.
You receive: the previous ranked causes, the checks that were asked with the driver's answers, the vehicle
and the sound analysis. Manual passages come from the rag_search tool when you have it (search again with
the driver's answers in the query), or are listed in the message with their chunk_id in square brackets.
Never use knowledge that is not in a passage.

Task: update the probability of every cause using the answers, and add a new cause when an answer points
to something that is not in the list yet (for example "oil below the minimum mark" points to low oil).

Rules:
- Use ONLY the passages for facts. Every cause cites passage ids, copied exactly.
- If an answer contradicts a cause, lower it and say why in its evidence. Quote the answer.
- An answer the driver could not give ("je ne sais pas") is not evidence for or against a cause.
- A cause "label" must be one of the label ids given to you, or null.
- If the answers still do not separate the top causes and new checks are allowed, ask up to 3 new checks
  (do not repeat an earlier check). If new checks are not allowed, return "checks": [].
- If the retrieval level is not exact_model, say so in message_to_user.
- Order causes from the most to the least likely.
- message_to_user is the advice for the driver, in this order: (1) for the MOST likely cause, one concrete action the
  passages describe, with the page in brackets, for example (p. 225); (2) then at most two short lines for the less likely
  causes, each starting with the cause name and its own action; (3) if a less likely cause involves brakes, steering,
  overheating or oil pressure, one sentence telling the driver to be careful until it is ruled out. Use only actions that a
  passage describes; if no passage gives an action, say to have it checked at a garage. Do not give repair steps (another
  agent does that). If you ask checks, end with a sentence asking the driver to answer them.
- "name" is a short readable phrase in the driver's language, never a label id or a chunk id. Each "evidence" line is a
  short fact from a passage or a quote of the driver, not an id.
- The audio analysis ends with a "Reading guide" line: follow it (for example, when the sound is probably healthy, ask
  whether the noise is intermittent instead of assuming a fault).
- For EVERY earlier cause give a "status" that says what the driver's answers did to it:
  "confirmed" (an answer supports it), "contradicted" (an answer shows it is not the cause), or "not_tested" (no answer concerns it).
  A contradicted cause keeps a very low probability and its evidence line quotes the answer.
- Write name, q, why and message_to_user in the driver's language.

Example for one earlier cause:
Earlier cause: "Cosses de batterie oxydées", 0.30. The driver answered: "les cosses sont propres et serrées".
-> {"name": "Cosses de batterie oxydées", "label": null, "prob": 0.05, "status": "contradicted",
    "evidence": ["le conducteur dit que les cosses sont propres et serrées"], "sources": ["<chunk_id>"]}

Answer with JSON only:
{"causes": [{"name": str, "label": str|null, "prob": float, "status": "confirmed|contradicted|not_tested",
             "evidence": [str], "sources": ["chunk_id"]}],
 "checks": [{"q": str, "why": str, "answer_type": "yes_no|choice|free_text",
             "choices": [str], "source": "chunk_id"}],
 "message_to_user": str}
Probabilities across causes sum to 1.

How to work when you have the rag_search tool:
1. Call it ONCE. The query is ONE full sentence in the driver's own words (for example: le démarreur ne tourne pas, j'entends un clic), not a list of keywords.
2. When you receive the passages, STOP calling tools and write the final answer.
3. The final answer is ONLY the JSON object described above: no sentence before or after it, no markdown.
Copy every chunk_id exactly as it appears in the passages.
```

## Input variables

User message built by the code: same as P4, plus previous causes, checks with answers, and whether new checks are allowed (round < 2).
The `status` field is read by the code only (a `contradicted` cause is capped at 0.05); it is not part of `agent2_output`.

| Variable | Source |
|---|---|
| previous causes | last item of case `diagnosis` |
| checks + answers | `diagnosis[*].checks` joined with `check_answers` by `check_id` |
| new checks allowed | `round < 2` |

## Test inputs

| Case | Input | Expected |
|---|---|---|
| answers confirm top cause | case with 3 yes answers | top cause prob up, `conclusive = true`, `checks = []` |
| answer points to new cause | "oil below the minimum mark" | a low-oil cause is added with a cited passage |
| round cap | `round = 2`, still unclear | `conclusive = false`, `checks = []` |

## Versions

| Version | Date | Change | Why (observed failure) | Effect |
|---|---|---|---|---|
| v1 | 2026-10-16 | first version; adds the "add a new cause" rule | prototype re-ranked only the 3 earlier causes, so "oil below minimum" never produced a low-oil cause and the result was over-confident (0.80) | to measure on the real model |
| v2 | 2026-10-16 | works with the rag_search tool; most likely cause first, caution sentence for less likely risky causes | same team decision as P4 v2 | to measure |
| v2.1 | 2026-10-16 | rule: readable cause names, evidence as short facts | local 7B model answered with label ids ('dead_battery') as names and chunk ids as evidence | names readable; code also maps label ids to names |
| v2.2 | 2026-10-16 | rule: follow the audio "Reading guide" line (computed from Agent 1's decision table) | Agent 1's integration guide defines how to read label + anomaly score + confidence | to measure |
| v2.3 | 2026-10-16 | explicit working procedure when the rag_search tool exists (one search, then the final JSON only, ids copied exactly); temperature 0 in the n8n model node | local 7B model (qwen2.5:7b) as tool-calling agent: in an offline replica of the n8n agent loop 3 of 6 runs usable (empty final message after the tool result x2, endless tool calls x1), plus 0 of about 25 in n8n | 12 of 12 usable (offline replica), 6 of 6 at temperature 0, about 14 s |
| v2.4 | 2026-10-15 | per-cause `status` (confirmed / contradicted / not_tested) with a worked example; code caps a contradicted cause at 0.05 | the 7B model ignored contradicting answers: driver says "les cosses sont propres et serrées", the cause "cosses mal serrées" stayed at 0.30 | contradiction handled in **1/8** runs (v2.3) -> 3/8 (free-text rule + example) -> **7/8** (status field + code cap), same case, 8 runs each |
| v2.5 | 2026-10-16 | message_to_user = ordered advice: concrete action for the most likely cause with its page, one line per less likely cause, caution for risky ones, no repair steps (Agent 3's job) | the message only asked questions and never said what to do (team decision: recommend most likely first, then less likely) | to measure on the 4 manual cases |

## Failure cases and before / after

**1. Contradicting answers were ignored (v2.3 -> v2.4).** Case `clio_starter_pass2.json`: the driver answers "oui, les cosses sont propres et serrées" to the terminals check.
The cause "cosses de batterie mal serrées, débranchées ou oxydées" should drop. Metric: probability left on the terminals causes (<= 0.15 counts as handled), 8 runs per variant, local 7B model.

| Variant | Handled | Typical answer |
|---|---|---|
| v2.3 (current at the time) | **1/8** | `0.50 batterie déchargée; 0.30 cosses de batterie mal serrées...; 0.20 problème de charge` |
| free-text rule "work cause by cause" + worked example | 3/8 | same, terminals at 0.25 in 5 of 8 runs |
| **v2.4: `status` per cause (confirmed / contradicted / not_tested) + example; code caps a contradicted cause at 0.05** | **7/8** | `0.43 Déchargée ou hors d’usage; 0.29 Circuit défectueux; 0.21 Alternateur défectueux; 0.07 Cosses de batterie...` |

Technique: structured reasoning (the model must classify every earlier cause) + a deterministic consequence in code. A free-text instruction was not enough for a 7B model.

**2. Empty answers in pass 2 (workflow fix, not a prompt change).** In n8n the agent repeated the same search 3 times and returned an empty string (0/4 usable). A safety net in the workflow answers once, in JSON mode, from the passages the agent already retrieved: 6/6 usable.

**3. Open:** the model still sometimes lowers the right cause too (the battery) when the driver says it "looks new". Needs more cases to tune.

## Failure to watch

Ignores contradictory answers. Treats "I don't know" as a "no".
