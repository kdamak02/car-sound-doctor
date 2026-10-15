import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from agent3_repair.guardrails.checks import check, insufficient_information  # noqa: E402
from contracts.validate import errors  # noqa: E402

EX = ROOT / "contracts" / "examples"
A2 = json.loads((EX / "agent2_output.json").read_text("utf-8"))
A3 = json.loads((EX / "agent3_output.json").read_text("utf-8"))
RETRIEVED = [p["chunk_id"] for p in
             json.loads((EX / "rag_search_response.json").read_text("utf-8"))["passages"]]


def test_examples_pass():
    assert check("agent2_output", A2, RETRIEVED) == []
    assert check("agent3_output", A3, RETRIEVED) == []


def test_invented_citation_rejected():
    bad = copy.deepcopy(A3)
    bad["steps"][0]["citation"]["chunk_id"] = "made_up_chunk"
    assert any(p.startswith("R2") for p in check("agent3_output", bad, RETRIEVED))


def test_brake_fault_cannot_say_keep_driving():
    bad = copy.deepcopy(A3)
    bad["fault"] = "Plaquettes de frein usées"
    bad["message_to_user"] = "Vous pouvez rouler encore quelques jours."
    probs = check("agent3_output", bad, RETRIEVED)
    assert any(p.startswith("R3") for p in probs)


def test_low_confidence_requires_mechanic():
    bad = copy.deepcopy(A3)
    bad["confidence"], bad["see_mechanic"] = "low", False
    assert any(p.startswith("R4") for p in check("agent3_output", bad, RETRIEVED))


def test_fallback_is_valid():
    assert errors("agent3_output", insufficient_information("courroie")) == []
