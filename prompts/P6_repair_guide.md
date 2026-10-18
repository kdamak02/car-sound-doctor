# P6 — Repair guide (Agent 3, group C)

| | |
|---|---|
| Owner | group C (`agent3_repair/`); this page was written by B2 from `agent3_repair/prompts/agent3_repair_guide_v4.md` and from our end-to-end runs |
| Used in | Agent 3 service `POST :8003/generate` (`agent3_repair/agent3_model.py`), called by the n8n workflow when Agent 2 is conclusive or has no checks left |
| Model | Groq `qwen/qwen3.8-27b` (vision-aware: images of cited PDF pages when legible) |
| Technique | evidence-grounded generation + derived applicability (exact/platform/make/generic) + named-part grounding + four-status ladder (`guide_available`, `diagnosis_only`, `insufficient_information`, `professional_assistance_required`) + JSON Schema + code validation (`agent3_repair/validation/validate_agent3.py`) |
| Output contract | `agent3_repair/schemas/agent3_output.schema.json` |

The full prompt (runtime instructions, rules, examples) is in `agent3_repair/prompts/agent3_repair_guide_v4.md`; it is not copied here to avoid two versions.

## Versions

The code refers to P6 v1 to v4 (`agent3_repair/agent3_model.py`), but only the v4 file is in the repository. The version history, the failures that caused v2, v3 and v4, and their before/after are **to be completed by group C**.
What v4 states about its own failure modes: applicability overstated; vague actions or unsupported parts; diagrams missed by text extraction; abstention versus guess when no procedure exists.

## Failure cases observed by B2 in the full chain (2026-10-18, details in `agent2_diagnosis/NOTE_FOR_AGENT3.md`)

1. A starter or battery diagnosis gets a generic "disconnect the battery" guide (iFixit) with status `guide_available`; the validator accepts it because the sources are cited, not because they match the fault.
2. A step can be a raw title ("[How to Disconnect a Car or Truck Battery | Step 1: Step 1]").
3. A "Critical brake issue detected" warning appeared on an oil-pressure case.
4. The language of the steps and limits is not always the case language (`fr`).
