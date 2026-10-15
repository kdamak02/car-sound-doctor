# Prompt library (annex of the report)

One file per prompt. Never edit a prompt only inside n8n: change the file, add a version line,
then paste it into n8n.

| ID | File | Used by | Technique | Model tier |
|---|---|---|---|---|
| P1 | `P1_extraction.md` | n8n, before Agent 1 | few-shot + strict JSON | small (Haiku-class) |
| P2 | `P2_clarification.md` | n8n | role + rules | small |
| P3 | `P3_audio_description.md` | Agent 2 input | template | — |
| P4 | `P4_checks_generation.md` | Agent 2 pass 1 | role + reasoning + citation required | Sonnet-class |
| P5 | `P5_reranking.md` | Agent 2 pass 2 | structured reasoning over answers | Sonnet-class |
| P6 | `P6_repair_guide.md` | Agent 3 | guardrails + JSON | Sonnet-class |
| P7 | `P7_final_message.md` | Telegram + TTS | length and style limits | small |

Copy `_TEMPLATE.md` to start a prompt. The "Versions" table is what the jury wants to see:
what failed, what you changed, and the effect.
