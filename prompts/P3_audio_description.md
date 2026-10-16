# P3 — Audio result as text for the LLM

| | |
|---|---|
| Owner | B2 |
| Used in | Agent 2, both passes (inserted in the user message of P4 and P5). Owner of this prompt: B2 (see Agent 1's INTEGRATION.md section 4) |
| Model | none (plain template, filled by code) |
| Settings | – |
| Technique | template |
| Output contract | input: `contracts/agent1_output.schema.json` |

## Prompt (current version)

```text
AUDIO ANALYSIS (automatic, may be wrong):
- Most likely sound class: {{label_name}} (probability {{prob}}, confidence {{confidence}}).
- Other possibilities: {{others}}.
- Unusualness compared with healthy engines: {{unusualness}}.
- Measured facts: {{summary}}
- Warnings: {{warnings}}
- Reading guide: {{reading}}
Treat this as a hypothesis to verify with the driver's checks, never as a diagnosis.
The audio model only knows a limited set of sound classes: other faults are possible.
```

## Input variables

| Variable | Source |
|---|---|
| `label_name` | name of `audio.label` in `labels.json`, in the driver's language (`fr`, else `en`) |
| `prob`, `others` | `audio.top3`, as percentages |
| `confidence` | `audio.confidence` (low, medium, high) |
| `unusualness` | `audio.anomaly_score`, a **percentile against healthy engines** (0.5 = typical healthy sound): "very high" from 0.95, "high" from 0.80, else "normal range" |
| `summary` | `audio.description.summary` |
| `warnings` | `audio.warnings`, or "none" |
| `reading` | computed in code from the rules of Agent 1's integration guide (below), or "none" |

Reading guide rules (computed in code, not left to the model):

| Situation | Line given to the model |
|---|---|
| `confidence` is low | audio not conclusive: rely on the description and the checks more than on the sound |
| label is a fault and `anomaly_score` >= 0.80 | class and anomaly agree: start from the most likely classes |
| label starts with `normal` and `anomaly_score` < 0.80 | probably healthy: ask if the noise is intermittent, suggest recording again |
| label starts with `normal` and `anomaly_score` >= 0.95 | very unusual sound outside the trained classes: also consider the hypothesis-only faults of `labels.json` and separate them with checks |

If `audio.status` is `unusable_audio` the text is replaced by: "No usable audio: rely on the user's description only." (n8n normally asks for a new recording before Agent 2.)

## Test inputs

| Case | Input | Expected |
|---|---|---|
| mock case | `contracts/examples/agent1_output.json` | French class name, probability 71 %, unusualness "high", reading guide "agree" |
| unusable | `{"status": "unusable_audio", ...}` | text-only sentence, no crash |

## Versions

| Version | Date | Change | Why (observed failure) | Effect |
|---|---|---|---|---|
| v1 | 2026-10-16 | first version | – | – |
| v2 | 2026-10-16 | aligned with Agent 1's INTEGRATION.md: anomaly score read as a percentile (bands), French label names, warnings, reading guide computed from the decision table | v1 read `anomaly_score` as 0 = healthy, 1 = very unusual (wrong: it is a percentile against healthy engines) and gave the model raw numbers to interpret | to measure |

## Failure cases and before / after

**Failure: the audio score was read the wrong way.** Agent 1 defines `anomaly_score` as a *percentile against healthy engines* (0.5 = a typical
healthy sound). P3 v1 told the model "0 = healthy engine, 1 = very unusual" and gave raw numbers to interpret.

| | Text given to the model (mock case) |
|---|---|
| Before (v1) | `classifier confidence: medium; anomaly score: 0.82 (0 = healthy engine, 1 = very unusual)` |
| After (v2) | `Unusualness compared with healthy engines: high` and `Reading guide: The sound class and the anomaly score agree: start from the most likely classes.` |

The rules that produce the reading guide are plain code (table above), so they are tested without a model (`test_audio_reading_guide_follows_agent1_rules`,
same cases in the Node test). Effect on the model's answers is not measured separately: on a "probably healthy" case (`clio_probably_healthy_pass1.json`)
the 7B model asks whether the noise is intermittent, as the guide says, but still invents fault causes.

## Failure to watch

The LLM over-interprets the numbers (treats a 0.62 classifier score as a diagnosis). The template says "a hint and not a certainty" and gives the confidence next to the label.
