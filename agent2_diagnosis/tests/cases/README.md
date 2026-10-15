# Agent 2 test cases (input case record + expected behaviour)

Each file is a `contracts/case_record.schema.json` object (checked by the tests). Send one to the workflow:

```bash
curl -X POST http://localhost:5678/webhook/agent2-diagnose -H "Content-Type: application/json; charset=utf-8" \
     --data-binary @agent2_diagnosis/tests/cases/clio_starter_pass1.json
# repeat a case N times and count usable answers:
python agent2_diagnosis/tests/reliability.py agent2_diagnosis/tests/cases/clio_starter_pass1.json 8
```

| Case | Round | Scenario | Expected from Agent 2 | Why |
|---|---|---|---|---|
| `clio_starter_pass1.json` | 0 | Clio 2015 diesel, key turned: one click, starter does not turn, battery light | `pass 1`, `conclusive false`, 2-3 causes about the battery / terminals / charging (labels `dead_battery`), 2-3 checks, every check cites `renault_clio4_2012-2016_fr.pdf` p. 225, `safety_stop false` | answer is in the Clio troubleshooting table |
| `clio_starter_pass2.json` | 1 | same case, the driver says the battery looks new and the terminals are clean and tight | `pass 2`; the terminals cause is lowered (<= 0.15); no cause is dropped without a cited reason; `conclusive` only if the top cause is >= 0.6 and 0.2 ahead | contradicting answers must lower a cause (prompt P5) |
| `clio_belt_pass1.json` | 0 | Clio, squeal at cold start, audio class `belt` | **`insufficient`** (no passage of any owner's manual explains belt noise). Today the 7B model still returns unsupported causes: known weakness, see `tests/eval_results.md` and CHECKPOINT | out-of-corpus case |
| `clio_belt_pass2.json` | 1 | same, with answers | same as above | out-of-corpus case |
| `clio_probably_healthy_pass1.json` | 0 | Clio, short click then normal start, class `normal_startup`, anomaly 0.55 | the answer asks whether the noise is intermittent (reading guide of P3); no invented fault with high confidence | audio says "probably healthy" |

Not covered yet (needs a case file and the team's agreement): brake fault (`safety_stop` true), user who never answers (handled by n8n timeout), unusable audio (n8n asks for a new recording), prompt injection in the user text.
