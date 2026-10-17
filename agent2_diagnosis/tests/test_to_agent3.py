"""Agent 2 -> Agent 3 mapper: every test case maps to something Agent 3's own input schema accepts."""
import json
from pathlib import Path

import jsonschema

from agent2_diagnosis.to_agent3 import to_agent3

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "agent3_repair/schemas/agent3_input.schema.json").read_text("utf-8"))
OUT = {"pass": 2, "conclusive": True, "checks": [], "causes": [
    {"name": "Batterie déchargée", "label": "dead_battery", "prob": 0.9, "evidence": ["le conducteur dit : les cosses sont propres", "clio4ph1fr_p225_c1", "passage clio4ph1fr_p225_c1"]},
    {"name": "Cosses", "label": None, "prob": 0.1, "evidence": []}]}


def case(name):
    return json.loads((ROOT / "agent2_diagnosis/tests/cases" / name).read_text("utf-8"))


def test_every_case_maps_to_a_valid_agent3_input():
    for f in sorted((ROOT / "agent2_diagnosis/tests/cases").glob("*.json")):
        jsonschema.validate(to_agent3(json.loads(f.read_text("utf-8")), OUT), SCHEMA)


def test_pass2_case_carries_checks_with_answers_and_clean_evidence():
    c = case("clio_starter_pass2.json")
    g = to_agent3(c, OUT)
    assert g["diagnosis"]["status"] == "confirmed" and g["language"] == "fr" and g["vehicle"]["year"] == 2015
    assert g["audio"] == {"class": "dead_battery", "confidence": 0.7}
    assert len(g["checks"]) == len(c["check_answers"]) and all(k["answer"] != "Not checked" for k in g["checks"])
    assert g["diagnosis"]["causes"][0]["evidence"] == ["le conducteur dit : les cosses sont propres"]  # chunk ids removed


def test_status_follows_conclusive_and_remaining_checks():
    c = case("clio_starter_pass1.json")
    assert to_agent3(c, {**OUT, "conclusive": False, "checks": [{"id": "c1"}]})["diagnosis"]["status"] == "probable"
    assert to_agent3(c, {**OUT, "conclusive": False, "checks": []})["diagnosis"]["status"] == "inconclusive"
