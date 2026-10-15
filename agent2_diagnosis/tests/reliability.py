"""Run a case N times against the n8n webhook and report how often the answer is usable.
python agent2_diagnosis/tests/reliability.py agent2_diagnosis/tests/cases/clio_starter_pass1.json 8
usable = HTTP 200 and a real agent2_output (causes present); insufficient = guardrails refused; error = n8n failed.
"""
import json
import sys
import time
from collections import Counter
from pathlib import Path

import requests

URL = "http://localhost:5678/webhook/agent2-diagnose"
case = Path(sys.argv[1]).read_bytes()
n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
outcomes, times, names = Counter(), [], []
for i in range(n):
    t = time.time()
    try:
        r = requests.post(URL, data=case, headers={"Content-Type": "application/json; charset=utf-8"}, timeout=290)
        body = r.json()
    except Exception as e:
        body, r = {"message": f"client error {e.__class__.__name__}"}, None
    dt = time.time() - t
    if r is not None and r.status_code == 200 and body.get("causes"):
        kind = "usable"; names.append([c["name"][:28] for c in body["causes"]][:2])
    elif body.get("insufficient"):
        kind = "insufficient"
    else:
        kind = "error"
    outcomes[kind] += 1
    times.append(dt)
    print(f"run {i + 1}: {kind:<12} {dt:5.1f}s  checks={len(body.get('checks', []))} conclusive={body.get('conclusive')}", flush=True)
print(dict(outcomes), f"median {sorted(times)[len(times) // 2]:.1f}s")
for x in names[:3]:
    print("  e.g.", x)
