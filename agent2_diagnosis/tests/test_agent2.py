"""
Agent 2 tests: no network, no LLM key. The RAG service and the LLM are replaced by stubs;
the passages are the ones of contracts/examples/rag_search_response.json (the mock /search answer).
Run from the repo root:  pytest -q agent2_diagnosis/tests
"""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent2_diagnosis import agent2  # noqa: E402
from contracts.validate import errors  # noqa: E402

EX = ROOT / "contracts/examples"
SEARCH_RES = json.loads((EX / "rag_search_response.json").read_text("utf-8"))
P1, P2, P3 = (p["chunk_id"] for p in SEARCH_RES["passages"])  # belt symptom, belt procedure, obdex belt


def search(query, vehicle, top_k=5):
    return copy.deepcopy(SEARCH_RES)


def fresh_case():
    c = json.loads((EX / "case_record.json").read_text("utf-8"))
    for k in ("diagnosis", "check_answers", "repair"):
        c.pop(k, None)
    c["round"] = 0
    return c


def llm_returning(obj):
    return lambda system, user: json.dumps(obj)


GOOD = {"causes": [{"name": "Courroie usée", "label": "belt", "prob": 7, "evidence": ["audio: belt 0.71"], "sources": [P1, "not_a_chunk"]},
                   {"name": "Direction assistée", "label": "power_steering", "prob": 1, "evidence": [], "sources": [P3]},
                   {"name": "Invented cause", "label": "belt", "prob": 5, "evidence": [], "sources": ["not_a_chunk"]},
                   {"name": "No citation", "label": None, "prob": 5, "evidence": [], "sources": []}],
        "checks": [{"q": "Le bruit augmente-t-il en tournant le volant ?", "why": "w", "answer_type": "yes_no", "source": P3},
                   {"q": "Fissures sur la courroie ?", "why": "w", "answer_type": "yes_no", "source": P2},
                   {"q": "Uncited check", "why": "w", "answer_type": "yes_no", "source": "not_a_chunk"},
                   {"q": "Choix ?", "why": "w", "answer_type": "choice", "choices": [], "source": P1}],
        "message_to_user": "Merci ! Répondez :"}


def test_pass1_grounded_and_valid():
    out = agent2.diagnose(fresh_case(), llm=llm_returning(GOOD), search=search)
    assert errors("agent2_output", out) == []
    assert [c["name"] for c in out["causes"]] == ["Courroie usée", "Direction assistée"]  # invented + uncited dropped
    assert abs(sum(c["prob"] for c in out["causes"]) - 1) < 0.01
    assert [c["id"] for c in out["checks"]] == ["c1", "c2", "c3"]  # uncited check dropped, ids sequential
    assert out["checks"][2]["answer_type"] == "free_text"  # choice without choices falls back
    assert out["pass"] == 1 and out["conclusive"] is False
    assert out["safety_stop"] is False  # power steering pump at 12% does not stop the driver (matches the contract example)
    assert all(c["citation"]["chunk_id"] in (P1, P2, P3) for c in out["checks"])


def test_unknown_label_becomes_null():
    g = copy.deepcopy(GOOD)
    g["causes"][0]["label"] = "flux_capacitor"
    assert agent2.diagnose(fresh_case(), llm=llm_returning(g), search=search)["causes"][0]["label"] is None


def test_safety_stop_follows_the_most_likely_cause():
    g = copy.deepcopy(GOOD)
    g["causes"][1].update(name="Plaquettes de frein usées", label="worn_brakes", prob=4)  # 36%, not the top cause
    assert agent2.diagnose(fresh_case(), llm=llm_returning(g), search=search)["safety_stop"] is False
    g["causes"][1]["prob"] = 9  # now the most likely cause
    assert agent2.diagnose(fresh_case(), llm=llm_returning(g), search=search)["safety_stop"] is True
    g["causes"][0].update(prob=20, name="Surchauffe moteur", label=None)
    assert agent2.diagnose(fresh_case(), llm=llm_returning(g), search=search)["safety_stop"] is True


def answered_case(round_):
    c = fresh_case()
    first = agent2.diagnose(c, llm=llm_returning(GOOD), search=search)
    c.update(diagnosis=[first], round=round_, check_answers=[{"check_id": "c1", "answer": "non", "round": 1}])
    return c


def test_pass2_conclusive_clears_checks():
    sure = {"causes": [{"name": "Courroie usée", "label": "belt", "prob": 0.8, "evidence": ["c1: non"], "sources": [P1]},
                       {"name": "Autre", "label": None, "prob": 0.2, "evidence": [], "sources": [P3]}],
            "checks": [{"q": "q", "why": "w", "answer_type": "yes_no", "source": P1}]}
    out = agent2.diagnose(answered_case(1), llm=llm_returning(sure), search=search)
    assert out["pass"] == 2 and out["conclusive"] is True and out["checks"] == []
    assert errors("agent2_output", out) == []


UNCLEAR = copy.deepcopy(GOOD)
UNCLEAR["causes"][0]["prob"], UNCLEAR["causes"][1]["prob"] = 5, 4  # 56% / 44%: answers did not separate them


def test_pass2_unclear_asks_new_checks_with_next_ids():
    unclear = UNCLEAR
    out = agent2.diagnose(answered_case(1), llm=llm_returning(unclear), search=search)
    assert out["conclusive"] is False and [c["id"] for c in out["checks"]][0] == "c4"  # continues after c1-c3


def test_round_cap_no_new_checks():
    out = agent2.diagnose(answered_case(2), llm=llm_returning(UNCLEAR), search=search)
    assert out["pass"] == 3 and out["conclusive"] is False and out["checks"] == []


def test_nothing_grounded_raises():
    with pytest.raises(agent2.Insufficient):
        agent2.diagnose(fresh_case(), llm=llm_returning({"causes": [], "checks": []}), search=search)
    with pytest.raises(agent2.Insufficient):
        agent2.diagnose(fresh_case(), llm=lambda s, u: "not json at all", search=search)
    with pytest.raises(agent2.Insufficient):
        agent2.diagnose(fresh_case(), llm=llm_returning(GOOD), search=lambda q, v, k=5: {"fallback_level": "none", "passages": []})


def test_user_message_content_and_fallback_level():
    seen = {}

    def spy(system, user):
        seen.update(system=system, user=user)
        return json.dumps(GOOD)

    def generic(q, v, k=5):
        return {**copy.deepcopy(SEARCH_RES), "fallback_level": "generic"}

    agent2.diagnose(fresh_case(), llm=spy, search=generic)
    assert "Courroie qui siffle (probability 71 %" in seen["user"] and "Unusualness compared with healthy engines: high" in seen["user"]
    assert "Retrieval level: generic" in seen["user"] and f"[{P1}]" in seen["user"]
    assert "rank the 2 to 5 most likely causes" in seen["system"]  # P4 loaded from prompts/


def test_unusable_audio_does_not_crash():
    c = fresh_case()
    c["audio"] = {"status": "unusable_audio", "label": None}
    assert "No usable audio" in agent2.describe_audio(c["audio"])
    assert agent2.diagnose(c, llm=llm_returning(GOOD), search=search)["pass"] == 1


def test_sloppy_ids_are_cleaned():
    g = copy.deepcopy(GOOD)
    g["causes"][0]["sources"] = [f" [{P1}] "]
    g["checks"][0]["source"] = f" {P3}"
    out = agent2.diagnose(fresh_case(), llm=llm_returning(g), search=search)
    assert out["causes"][0]["name"] == "Courroie usée" and out["checks"][0]["citation"]["chunk_id"] == P3


def test_machine_id_names_become_readable():
    g = copy.deepcopy(GOOD)
    g["causes"][0].update(name="dead_battery", label="dead_battery")
    g["causes"][1].update(name="obdex_P2503", label=None)
    out = agent2.diagnose(fresh_case(), llm=llm_returning(g), search=search)  # language fr
    assert out["causes"][0]["name"] == "Batterie faible" and out["causes"][1]["name"] == "obdex P2503"
    c = fresh_case()
    c["user_input"]["language"] = "en"
    assert agent2.diagnose(c, llm=llm_returning(g), search=search)["causes"][0]["name"] == "Weak / dead battery"
    g["causes"][1]["name"] = "Surchauffe"  # an ordinary word is never rewritten, even with a valid label
    g["causes"][1]["label"] = "belt"
    assert agent2.diagnose(fresh_case(), llm=llm_returning(g), search=search)["causes"][1]["name"] == "Surchauffe"


def test_audio_reading_guide_follows_agent1_rules():
    base = {"status": "ok", "label": "belt", "anomaly_score": 0.9, "confidence": "high",
            "top3": [{"label": "belt", "prob": 0.8}], "description": {"summary": "s"}}
    assert "agree" in agent2.audio_reading(base)
    assert agent2.audio_reading({**base, "label": "normal", "anomaly_score": 0.5}).startswith("The engine probably sounds healthy")
    outside = agent2.audio_reading({**base, "label": "normal", "anomaly_score": 0.97})
    assert "outside" in outside and "timing chain" in outside
    assert agent2.audio_reading({**base, "label": "normal", "anomaly_score": 0.88}) == "none"  # between the two rules
    assert "not conclusive" in agent2.audio_reading({**base, "confidence": "low", "anomaly_score": 0.5})
    assert agent2.audio_reading({**base, "anomaly_score": 0.6}) == "none"  # a fault class but a healthy-looking sound
    # the anomaly score is a percentile against healthy engines: bands, not raw numbers
    assert [agent2.unusualness(x) for x in (0.5, 0.8, 0.95, None)] == ["normal range", "high", "very high", "unknown"]
    text = agent2.describe_audio({**base, "warnings": ["unknown context"]}, "en")
    assert "Most likely sound class: Squealing belt (probability 80 %" in text and "Warnings: unknown context" in text


def test_contradicted_cause_is_capped():
    """P5 v2.4: the model labels each cause; a contradicted one cannot keep a high probability."""
    sure = {"causes": [{"name": "Courroie usée", "label": "belt", "prob": 0.5, "status": "contradicted", "evidence": ["non"], "sources": [P1]},
                       {"name": "Autre", "label": None, "prob": 0.5, "status": "confirmed", "evidence": [], "sources": [P3]}], "checks": []}
    out = agent2.diagnose(answered_case(1), llm=llm_returning(sure), search=search)
    by_name = {c["name"]: c["prob"] for c in out["causes"]}
    assert by_name["Courroie usée"] <= 0.1 and by_name["Autre"] > 0.9 and out["causes"][0]["name"] == "Autre"
    assert "status" not in out["causes"][0]  # internal field, not part of agent2_output
    # no status (pass 1, or a model that skips it): nothing changes
    plain = {"causes": [{"name": "A", "label": None, "prob": 0.5, "evidence": [], "sources": [P1]}, {"name": "B", "label": None, "prob": 0.5, "evidence": [], "sources": [P3]}], "checks": []}
    assert [c["prob"] for c in agent2.diagnose(fresh_case(), llm=llm_returning(plain), search=search)["causes"]] == [0.5, 0.5]


def test_labels_loader_understands_both_formats(tmp_path):
    from agent2_diagnosis.labels import load_labels

    (tmp_path / "contracts").mkdir()
    (tmp_path / "agent1_audio/results").mkdir(parents=True)
    lab = lambda i: {"id": i, "context": "idle", "fr": i, "en": i}  # noqa: E731
    # old format: untouched
    (tmp_path / "contracts/labels.json").write_text(json.dumps({"labels": [lab("a")], "hypotheses_only": {"ids": ["z"]}}))
    assert load_labels(tmp_path)["hypotheses_only"]["ids"] == ["z"]
    # new format: hypotheses = labels that are not classes of Agent 1's model
    (tmp_path / "contracts/labels.json").write_text(json.dumps({"labels": [lab("a"), lab("b"), lab("c")]}))
    (tmp_path / "agent1_audio/results/ast.json").write_text(json.dumps({"labels": ["a", "b"]}))
    assert load_labels(tmp_path)["hypotheses_only"]["ids"] == ["c"]
    (tmp_path / "agent1_audio/results/ast.json").unlink()  # no result file: nothing is claimed
    assert load_labels(tmp_path)["hypotheses_only"]["ids"] == []


def test_support_check_drops_cause_whose_citation_does_not_come_back():
    by_id = {"a": {}, "b": {}}
    raw = [{"name": "belt", "prob": 0.7, "sources": ["a"]}, {"name": "battery", "prob": 0.3, "sources": ["b"]}]
    search = lambda q, v, k: {"passages": [{"chunk_id": "b"}] if q == "battery" else []}  # noqa: E731
    kept = agent2.supported_causes(raw, by_id, {}, search)
    assert [c["name"] for c in kept] == ["battery"]
