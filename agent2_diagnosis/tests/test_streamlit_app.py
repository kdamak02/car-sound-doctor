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


def click(at, label):
    next(b for b in at.button if b.label == label).click()


def test_two_pass_flow():
    sent = []

    def fake_post(url, data, headers, timeout):
        sent.append(json.loads(data.decode("utf-8")))
        return FakeResponse(PASS1 if len(sent) == 1 else PASS2)

    with patch("requests.post", fake_post):
        at = AppTest.from_file(APP, default_timeout=30).run()
        assert not at.exception
        click(at, "Lancer l'Agent 2")
        at.run()
        assert not at.exception
        # pass 1 request is a valid case record, round 0, with the Agent 1 style audio block
        assert errors("case_record", sent[0]) == [] and sent[0]["round"] == 0 and sent[0]["audio"]["label"] == "dead_battery"
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
        at = AppTest.from_file(APP, default_timeout=30).run()
        click(at, "Lancer l'Agent 2")
        at.run()
        assert any(expected in e.value for e in at.error)


def test_insufficient_answer_is_shown():
    with patch("requests.post", lambda *a, **k: FakeResponse({"insufficient": True, "reason": "no manual passages"})):
        at = AppTest.from_file(APP, default_timeout=30).run()
        click(at, "Lancer l'Agent 2")
        at.run()
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
        at = AppTest.from_file(APP, default_timeout=30).run()
        at.toggle[0].set_value(True)
        at.run()
        click(at, "Lancer la chaîne")
        at.run()
        assert not at.exception
        text = " ".join(m.value for m in list(at.markdown) + list(at.info) + list(at.success) + list(at.error) + list(at.caption))
        assert "Agent 1" in text and "Débrancher la borne négative" in text and "guide_available" in text and "Coupez le contact" in text


def test_unusable_recording_asks_for_a_new_one():
    with patch("requests.post", lambda *a, **k: FakeResponse({"needs_new_audio": True, "message": "refaites un enregistrement"})):
        at = AppTest.from_file(APP, default_timeout=30).run()
        at.toggle[0].set_value(True)
        at.run()
        click(at, "Lancer la chaîne")
        at.run()
        assert not at.exception and any("refaites un enregistrement" in w.value for w in at.warning)
