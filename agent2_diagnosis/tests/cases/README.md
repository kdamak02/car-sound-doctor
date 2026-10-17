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
| `clio_oil_pressure_pass1.json` | 0 | Clio 2015 diesel, oil pressure light in corners, ticking at idle (class `low_oil`) | oil pressure cause first, checks cite the Clio manual p. 63 / 226, `safety_stop true` | in the Clio troubleshooting table |
| `clio_power_steering_pass1.json` | 0 | Clio, steering suddenly hard, whine when turning (class `power_steering`) | power steering pump first, checks cite p. 227 | in the Clio troubleshooting table. **Unstable with the 7B model**: it often cites label names (`power_steering`) instead of passage ids, so every cause is dropped and the case ends `insufficient` |
| `peugeot208_charge_light_pass1.json` | 0 | Peugeot 208 2016, battery charge light stays on (class `alternator`) | alternator / battery, checks cite the 208 manual p. 14 | in the 208 warning-light table |
