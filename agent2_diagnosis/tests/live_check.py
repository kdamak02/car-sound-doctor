"""Live end-to-end check of the three agents together (needs the whole stack running and ONE full-chain workflow active in n8n).

    python agent2_diagnosis/tests/live_check.py [clip.ogg|.wav] [--case clio_starter_pass1]

Default clip: data/audio/demo_clips/demo_failed_start.ogg (see HANDOVER.md "Test clips"). What it does, like the Telegram bot / test page:
  1. pass 1: voice note + case -> Agent 1 (sound) -> Agent 2 (causes + checks)
  2. answers the checks (yes/no -> "non", choice -> first option, text -> "je ne sais pas") -> pass 2 (or stops if Agent 2 is already conclusive)
  3. when Agent 2 is conclusive or has no checks left -> Agent 3 repair guide
Prints PASS / FAIL per agent and exits 1 if one failed. Takes 1 to 3 minutes. Costs one Agent 2 run on the chosen model (about 14,000 Groq tokens for two passes).
"""
import copy
import json
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
N8N = "http://localhost:5678/webhook"
args = [a for a in sys.argv[1:] if not a.startswith("--")]
case_name = sys.argv[sys.argv.index("--case") + 1] if "--case" in sys.argv else "clio_starter_pass1"
clip = Path(args[0]) if args and not args[0].startswith("clio") else ROOT / "data/audio/demo_clips/demo_failed_start.ogg"
results = {}


def verdict(name, ok, detail=""):
    results[name] = ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


if not clip.exists():
    sys.exit(f"clip not found: {clip}\nsee agent2_diagnosis/HANDOVER.md, section 'Test clips'")
if clip.suffix.lower() != ".ogg":  # send a Telegram-like voice note
    out = clip.with_suffix(".ogg")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(clip), "-ar", "48000", "-ac", "1", "-c:a", "libopus", "-b:a", "48k", str(out)], check=True)
    clip = out

print("Services")
for name, url in (("n8n", "http://localhost:5678/healthz"), ("RAG", "http://localhost:8002/health"), ("Agent 1", "http://localhost:8001/health"),
                  ("Agent 3", "http://127.0.0.1:8003/health")):
    try:
        ok = requests.get(url, timeout=5).ok
    except requests.RequestException:
        ok = False
    verdict(name + " up", ok)
if not all(results.values()):
    sys.exit("start the stack first: .\\agent2_diagnosis\\start_full_chain.ps1 (see HANDOVER.md)")

case = json.loads((ROOT / "agent2_diagnosis/tests/cases" / f"{case_name}.json").read_text("utf-8"))
case.pop("audio", None)  # Agent 1 fills it
print(f"\nPass 1: {clip.name} + case {case_name}")
t = time.time()
try:
    r = requests.post(f"{N8N}/agent2-diagnose-audio", files={"audio": (clip.name, clip.read_bytes(), "audio/ogg")},
                      data={"case": json.dumps(case, ensure_ascii=False)}, timeout=300).json()
except (requests.RequestException, ValueError) as e:
    sys.exit(f"FAIL  the audio webhook did not answer ({e}). Is a full-chain workflow ACTIVE in n8n?")
print(f"  ({round(time.time() - t)} s) reply keys: {list(r)}")
if "agent2" not in r:
    verdict("Agent 2 pass 1", False, str(r)[:200])
    sys.exit(1)
a1, a2, a3 = r.get("agent1"), r["agent2"], r.get("agent3")
verdict("Agent 1 analysed the clip", bool(a1) and a1.get("status") == "ok",
        f"{a1.get('label')} / {a1.get('confidence')} / unusualness {a1.get('anomaly_score')}" if a1 else "no analysis in the reply")
verdict("Agent 2 pass 1 gave cited causes", bool(a2.get("causes")), ", ".join(f"{c['name'][:30]} {round(c['prob'] * 100)}%" for c in a2.get("causes", [])))
cited = all(c.get("citation", {}).get("chunk_id") for c in a2.get("checks", []))
verdict("every check cites a manual passage", cited, f"{len(a2.get('checks', []))} checks")

last = a2
if a2.get("checks") and not a2.get("conclusive"):
    p2 = copy.deepcopy(case)
    if a1:
        p2["audio"] = a1
    p2.update(round=1, status="waiting_checks", diagnosis=[a2])
    p2["check_answers"] = [{"check_id": c["id"], "round": 1, "answer": ("non" if c["answer_type"] == "yes_no" else
                           (c.get("choices") or ["je ne sais pas"])[0] if c["answer_type"] == "choice" else "je ne sais pas")} for c in a2["checks"]]
    print("\nPass 2: the driver's answers are sent")
    t = time.time()
    r2 = requests.post(f"{N8N}/agent2-diagnose", data=json.dumps(p2, ensure_ascii=False).encode("utf-8"),
                       headers={"Content-Type": "application/json; charset=utf-8"}, timeout=300).json()
    print(f"  ({round(time.time() - t)} s) reply keys: {list(r2)}")
    last, a3 = r2.get("agent2"), r2.get("agent3")
    verdict("Agent 2 pass 2 re-ranked", bool(last) and bool(last.get("causes")),
            ", ".join(f"{c['name'][:30]} {round(c['prob'] * 100)}%" for c in (last or {}).get("causes", [])) or str(r2)[:200])

if last and (last.get("conclusive") or not last.get("checks")):
    g = (a3 or {}).get("guide")
    verdict("Agent 3 returned a guide", bool(g), f"status {g.get('status')}, {len((g.get('repair_guide') or {}).get('steps', []))} steps" if g else str(a3)[:200])
    if g:
        val = (a3 or {}).get("validation", {})
        verdict("Agent 3 validation passed", bool(val.get("valid")), f"citations {val.get('citations_valid')}, safety {val.get('safety_passed')}")
        print("  note: this checks that the chain works, not that the guide is specific to the fault (see NOTE_FOR_AGENT3.md)")
else:
    print("\nAgent 2 is not conclusive after the checks: no guide expected (the bot would say 'see a mechanic').")

print("\n" + ("ALL PASS" if all(results.values()) else "FAILED: " + ", ".join(k for k, v in results.items() if not v)))
sys.exit(0 if all(results.values()) else 1)
