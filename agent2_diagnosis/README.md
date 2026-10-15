# Agent 2 — diagnosis and physical checks (group B, owner B2)

LLM + RAG, runs as an AI Agent node in n8n, two passes.

**Input** (from the case record): `vehicle`, `symptom`, `audio` (Agent 1 output), and in pass 2 the
`check_answers`.
**Tool**: `POST /search` on the RAG service with `section: ["symptoms", "causes", "codes"]`.
**Output**: `contracts/agent2_output.schema.json`, every check with a citation of a retrieved chunk.

| Pass | Prompt | Job |
|---|---|---|
| 1 | `prompts/P3_audio_description.md` + `prompts/P4_checks_generation.md` | rank 3-5 causes, ask 3-5 checks the user can do without tools |
| 2 | `prompts/P5_reranking.md` | re-rank with the answers; `conclusive=false` and round < 2 → new checks, else go to Agent 3 |

Rules
- Classes in `contracts/labels.json → hypotheses_only` (knock, exhaust leak...) can be proposed
  as causes even if the audio model cannot detect them, but only with checks that confirm them.
- If a cause involves brakes, steering or overheating: `safety_stop = true`.
- Guardrails (`agent3_repair/guardrails/checks.py`, n8n `code_nodes/validate_output.js`) run on
  this output too.

Put here: test cases (input case + expected causes) in `tests/cases/`, and any helper script.
