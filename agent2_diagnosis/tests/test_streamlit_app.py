"""
Headless test of agent2_diagnosis/streamlit_app.py: the n8n webhook is replaced by a fake, so no Docker, no key, no quota.
Skipped when streamlit is not installed (CI).   Run: pytest -q agent2_diagnosis/tests/test_streamlit_app.py
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from contracts.validate import errors  # noqa: E402

APP = str(ROOT / "agent2_diagnosis/streamlit_app.py")
CIT = {"chunk_id": "peugeot208_fr_p143_c1", "source": "EXAMPLE.pdf", "page": 143, "verified": True}
PASS1 = {"pass": 1, "conclusive": False, "safety_stop": False, "message_to_user": "Merci, répondez :",
         "causes": [{"name": "Courroie usée", "label": "belt", "prob": 0.7, "evidence": ["audio: belt 0.78", "passage x"]},
                    {"name": "Galet tendeur", "label": None, "prob": 0.3, "evidence": ["passage y"]}],
         "checks": [{"id": "c1", "q": "Fissures sur la courroie ?", "why": "usure", "answer_type": "yes_no", "citation": CIT},
                    {"id": "c2", "q": "Le bruit change ?", "why": "charge", "answer_type": "choice",
                     "choices": ["augmente", "diminue"], "citation": {**CIT, "page": None}}]}
PASS2 = {"pass": 2, "conclusive": True, "safety_stop": False, "message_to_user": "Conclusion.", "checks": [],
         "causes": [{"name": "Courroie usée", "label": "belt", "prob": 0.9, "evidence": ["c1: oui"]}]}


class FakeResponse:
    def __init__(self, body, status=200):
        self._body, self.status_code, self.text = body, status, json.dumps(body)

    def json(self):
        return self._body


RUN = "Lancer les 3 agents"


def click(at, label):
    next(b for b in at.button if b.label == label).click()


def open_page():
    return AppTest.from_file(APP, default_timeout=30).run()


def run_agents(at):
    """Submit the form with the demo clip turned off (text only: deterministic whether or not the git-ignored clip exists)."""
    for c in at.checkbox:
        if "démonstration" in c.label:
            c.set_value(False)
    click(at, RUN)
    at.run()


def test_two_pass_flow():
    sent = []

    def fake_post(url, data, headers, timeout):
        sent.append(json.loads(data.decode("utf-8")))
        return FakeResponse(PASS1 if len(sent) == 1 else PASS2)

    with patch("requests.post", fake_post):
        at = open_page()
        assert not at.exception
        run_agents(at)
        assert not at.exception
        # pass 1 request is a valid case record, round 0 (no audio block: Agent 1 produces it from a recording)
        assert errors("case_record", sent[0]) == [] and sent[0]["round"] == 0 and "audio" not in sent[0]
        text = " ".join(m.value for m in at.markdown)
        assert "Courroie usée" in text and "70 %" in text and "Fissures sur la courroie" in text
        # the send button stays disabled until every check is answered
        assert next(b for b in at.button if b.label == "Envoyer mes réponses").disabled
        at.radio(key="ans_0_c1").set_value("oui")
        at.radio(key="ans_0_c2").set_value("augmente")
        at.run()
        click(at, "Envoyer mes réponses")
        at.run()
        assert not at.exception
        # pass 2 request: round 1, previous diagnosis and the answers attached
        req = sent[1]
        assert errors("case_record", req) == [], errors("case_record", req)
        assert req["round"] == 1 and req["diagnosis"][0]["pass"] == 1
        assert [(a["check_id"], a["answer"]) for a in req["check_answers"]] == [("c1", "oui"), ("c2", "augmente")]
        assert any("conclusif" in s.value for s in at.success)


@pytest.mark.parametrize("response,expected", [
    (FakeResponse({"code": 404, "message": "not registered"}, 404), "pas actif"),
    (FakeResponse({"message": "Error in workflow"}, 500), "quota"),
])
def test_n8n_errors_are_explained(response, expected):
    with patch("requests.post", lambda *a, **k: response):
        at = open_page()
        run_agents(at)
        assert any(expected in e.value for e in at.error)


def test_insufficient_answer_is_shown():
    with patch("requests.post", lambda *a, **k: FakeResponse({"insufficient": True, "reason": "no manual passages"})):
        at = open_page()
        run_agents(at)
        assert not at.exception and any("no manual passages" in w.value for w in at.warning)


def test_full_chain_shows_the_three_agents():
    a1 = {"status": "ok", "label": "dead_battery", "context": "startup", "confidence": "medium", "anomaly_score": 0.88,
          "top3": [{"label": "dead_battery", "prob": 0.7}, {"label": "bad_ignition", "prob": 0.2}],
          "description": {"summary": "Un clic sec."}}
    a3 = {"guide": {"status": "guide_available", "fault": {"name": "Batterie"}, "safety_warnings": ["Coupez le contact"],
                    "repair_guide": {"steps": [{"number": 1, "action": "Débrancher la borne négative", "source_id": "ifixit_1", "page": 3}]},
                    "limitations": ["guide générique"]},
          "validation": {"valid": True, "citations_valid": True, "safety_passed": True}}
    reply = {"agent1": a1, "agent2": PASS2, "agent3": a3}
    with patch("requests.post", lambda *a, **k: FakeResponse(reply)):
        at = open_page()
        run_agents(at)
        assert not at.exception
        text = " ".join(m.value for m in list(at.markdown) + list(at.metric) + list(at.info) + list(at.success) + list(at.error) + list(at.caption))
        # the three cards and the three sections each show their own output
        assert "Agent 1" in text and "Agent 2" in text and "Agent 3" in text
        assert "Batterie faible" in text  # Agent 1's class, in French
        assert "Débrancher la borne négative" in text and "guide_available" in text and "Coupez le contact" in text
        assert "Ce que l'Agent 2 a transmis" in " ".join(e.label for e in at.expander)


def test_unusable_recording_asks_for_a_new_one():
    with patch("requests.post", lambda *a, **k: FakeResponse({"needs_new_audio": True, "message": "refaites un enregistrement"})):
        at = open_page()
        run_agents(at)
        assert not at.exception and any("refaites un enregistrement" in w.value for w in at.warning)


def test_agent3_can_be_asked_before_the_checks_are_answered():
    a3 = {"guide": {"status": "diagnosis_only", "fault": {"name": "Batterie"}, "safety_warnings": [], "repair_guide": {"steps": []}, "limitations": []},
          "validation": {"valid": True, "citations_valid": True, "safety_passed": True}}
    sent = []

    def fake_post(url, **kw):
        sent.append((url, kw))
        return FakeResponse(PASS1) if "5678" in url else FakeResponse(a3)

    with patch("requests.post", fake_post):
        at = open_page()
        run_agents(at)  # pass 1: checks pending, so Agent 3 has not been called
        assert not at.exception and len(sent) == 1
        click(at, "Demander le guide à l'Agent 3 maintenant (diagnostic provisoire)")
        at.run()
        assert not at.exception and len(sent) == 2 and "8003" in sent[1][0]
        assert sent[1][1]["json"]["case_record"]["diagnosis"]["status"] == "probable"  # valid Agent 3 input, not conclusive yet
        assert any("diagnosis_only" in s.value for s in list(at.warning) + list(at.success))
