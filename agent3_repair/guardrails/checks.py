"""
Guardrails applied to Agent 2 and Agent 3 outputs before anything reaches the user.
Owner: C1. The same rules exist in JavaScript for the n8n Code node:
n8n/code_nodes/validate_output.js — keep both in sync.

Rules (ARCHITECTURE.md section 7):
  R1  output matches the contract schema
  R2  every check / step cites a chunk_id that was really retrieved for this case
  R3  safety faults (brakes, steering, overheating): never tell the user to keep driving,
      and Agent 3 must set safety_warnings
  R4  low confidence or mechanic_only  ->  see_mechanic = true
  R5  web results (verified = false) are allowed only if labelled as unverified in the text
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from contracts.validate import errors as schema_errors  # noqa: E402

SAFETY_WORDS = re.compile(r"frein|brake|direction|steering|surchauffe|overheat|temp[ée]rature",
                          re.I)
KEEP_DRIVING = re.compile(r"continuer [àa] rouler|vous pouvez rouler|keep driving|safe to drive|"
                          r"pouvez continuer", re.I)


def _citations(obj, agent):
    items = obj.get("checks", []) if agent == "agent2_output" else obj.get("steps", [])
    return [(i.get("id") or f"step {i.get('n')}", i.get("citation") or {}) for i in items]


def check(agent, obj, retrieved_ids):
    """agent: 'agent2_output' or 'agent3_output'. Returns a list of problems ([] = ok)."""
    problems = [f"R1 schema: {e}" for e in schema_errors(agent, obj)]
    if problems:
        return problems
    retrieved = set(retrieved_ids)
    for who, c in _citations(obj, agent):
        if c.get("chunk_id") not in retrieved:
            problems.append(f"R2 {who}: cites {c.get('chunk_id')!r}, not in the retrieved passages")
        if c.get("verified") is False and "non vérifié" not in str(obj).lower() \
                and "unverified" not in str(obj).lower():
            problems.append(f"R5 {who}: web source not labelled as unverified")

    text = " ".join(str(v) for v in obj.values())
    safety = SAFETY_WORDS.search(str(obj.get("fault", ""))) or any(
        SAFETY_WORDS.search(c.get("name", "")) for c in obj.get("causes", [])[:1])
    if safety and KEEP_DRIVING.search(text):
        problems.append("R3 safety fault: the answer suggests the car can still be driven")
    if agent == "agent3_output":
        if safety and not obj.get("safety_warnings"):
            problems.append("R3 safety fault without safety_warnings")
        if (obj["confidence"] == "low" or obj["difficulty"] == "mechanic_only") \
                and not obj["see_mechanic"]:
            problems.append("R4 low confidence or mechanic_only but see_mechanic is false")
        if obj["status"] == "ok" and not obj["steps"]:
            problems.append("R1 status ok but no steps")
    return problems


def insufficient_information(fault=""):
    """What to send when the guardrails reject the answer twice."""
    return {"status": "insufficient_information", "fault": fault, "steps": [], "tools": [],
            "difficulty": "mechanic_only", "confidence": "low", "see_mechanic": True,
            "safety_warnings": [],
            "message_to_user": "Je n'ai pas trouvé d'information fiable dans les manuels pour "
                               "ce cas. Par prudence, faites vérifier la voiture par un mécanicien."}
