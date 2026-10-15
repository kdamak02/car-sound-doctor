# Agent 3 — repair guide (group C, owner C1)

LLM + RAG, AI Agent node in n8n, followed by the guardrail validator.

**Input**: re-ranked causes (last Agent 2 pass), check answers, vehicle.
**Tool**: `POST /search` with `section: ["procedure", "safety", "maintenance"]`.
**Output**: `contracts/agent3_output.schema.json`. Prompt: `prompts/P6_repair_guide.md`,
voice summary `prompts/P7_final_message.md`.

## Guardrails

`guardrails/checks.py` (Python, tested) and `n8n/code_nodes/validate_output.js` (same rules for n8n):

| Rule | Check |
|---|---|
| R1 | output valid against the schema |
| R2 | every step cites a chunk really returned by `/search` for this case |
| R3 | brakes / steering / overheating: never "you can keep driving", safety warnings required |
| R4 | low confidence or `mechanic_only` → `see_mechanic = true` |
| R5 | web fallback results labelled "non vérifié" |

Flow in n8n: Agent 3 → validate → if invalid, retry once with the problems in the prompt →
still invalid → `insufficient_information()` answer.

```bash
pytest -q agent3_repair/tests      # from the repo root
```
