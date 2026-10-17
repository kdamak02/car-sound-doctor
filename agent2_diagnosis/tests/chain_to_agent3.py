"""End-to-end check Agent 2 -> Agent 3 (needs n8n with the Agent 2 workflow on :5678 and Agent 3 on :8003 with its GROQ key).
python agent2_diagnosis/tests/chain_to_agent3.py clio_starter_pass2      # a file name from tests/cases/ without .json
Prints Agent 2's causes, the hand-over record, then Agent 3's status, sources and validation."""
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from agent2_diagnosis.to_agent3 import to_agent3  # noqa: E402

case = json.loads((ROOT / "agent2_diagnosis/tests/cases" / f"{sys.argv[1]}.json").read_text("utf-8"))
t = time.time()
out = requests.post("http://localhost:5678/webhook/agent2-diagnose", data=json.dumps(case).encode(),
                    headers={"Content-Type": "application/json; charset=utf-8"}, timeout=290).json()
print("Agent 2:", round(time.time() - t), "s", "insufficient" if out.get("insufficient") else [(c["name"][:35], c["prob"]) for c in out["causes"]])
if out.get("insufficient"):
    sys.exit("Agent 2 gave no reliable answer: nothing to hand over")
rec = to_agent3(case, out)
print("hand-over:", rec["diagnosis"]["status"], rec["audio"], len(rec["checks"]), "checks")
r = requests.post("http://127.0.0.1:8003/generate", json={"case_record": rec, "retrieve": True}, timeout=280)
print("Agent 3: HTTP", r.status_code)
d = r.json()
if r.status_code != 200:
    sys.exit(str(d)[:400])
print("status:", d["guide"].get("status"), "| sources:", [(s.get("source_id"), s.get("page")) for s in d["retrieved_sources"]][:5])
print("validation:", {k: v for k, v in d["validation"].items() if k in ("valid", "citations_valid", "safety_passed", "errors")})
