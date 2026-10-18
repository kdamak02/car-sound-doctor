# Prompt library (annex of the report)

One file per prompt. Never edit a prompt only inside n8n: change the file, add a version line,
then paste it into n8n.

| ID | File | Used by | Technique | Model tier |
|---|---|---|---|---|
| P1 | `P1_extraction.md` | Telegram front (`Understand message (P1)`) | few-shot + strict JSON | small (Ollama qwen2.5:7b) |
| P2 | `P2_clarification.md` | Telegram front (`Handle update`) | role + rules + code check | small (Ollama qwen2.5:7b) |
| P3 | `P3_audio_description.md` | Agent 2 input | template | — |
| P4 | `P4_checks_generation.md` | Agent 2 pass 1 | role + reasoning + citation required | Sonnet-class |
| P5 | `P5_reranking.md` | Agent 2 pass 2 | structured reasoning over answers | Sonnet-class |
| P6 | `P6_repair_guide.md` | Agent 3 (group C; full text in `agent3_repair/prompts/`) | guardrails + JSON | Groq qwen |
| P7 | `P7_final_message.md` | Telegram front (`Reply`) | length and style limits + code guardrail | small (Ollama qwen2.5:7b) |

Copy `_TEMPLATE.md` to start a prompt. The "Versions" table is what the jury wants to see:
what failed, what you changed, and the effect.

`PROMPT_LIBRARY.md` = all prompts in one file (the deliverable). The evaluations are in `prompts/eval/` (P1: `run_p1.py`, `run_p1_retrieval.py`; P2 and P7: `run_p2_p7.py`);
`python prompts/eval/build_prompt_docs.py` regenerates P1, P2, P7, P6 and the single file from the prompt texts the workflows run (`tests/test_prompt_docs.py` checks there is no drift).
