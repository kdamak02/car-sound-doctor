"""
Streamlit test page for Agent 2. Owner: B2.

    pip install streamlit requests
    streamlit run agent2_diagnosis/streamlit_app.py

It plays the role of Telegram + Agent 1: you describe the car and the sound, the page builds a case record
(contracts/case_record.schema.json) and POSTs it to the Agent 2 n8n workflow (webhook /webhook/agent2-diagnose).
Pass 1 returns causes and checks; you answer the checks and send them back for pass 2 (max 2 rounds).
Needs: n8n running with the workflow "Agent 2 - diagnosis and checks" ACTIVE and the RAG service reachable by n8n.
"""
import copy
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent2_diagnosis.labels import load_labels  # noqa: E402

LABELS = load_labels(ROOT)
LABEL_NAME = {x["id"]: x["fr"] for x in LABELS["labels"] if x["id"] not in LABELS["hypotheses_only"]["ids"]}  # Agent 1 never outputs hypotheses
CONTEXTS = ["startup", "idle", "driving", "braking", "turning", "unknown"]
CASES = sorted((ROOT / "agent2_diagnosis/tests/cases").glob("*.json"))
MAX_ROUNDS = 2
DEFAULT_URL = os.getenv("AGENT2_WEBHOOK", "http://localhost:5678/webhook/agent2-diagnose")
ANSWERS_YN = ["oui", "non", "je ne sais pas"]

st.set_page_config(page_title="Agent 2 - test", page_icon="🔧", layout="wide")


# ------------------------------------------------------------------ helpers

def build_case(f: dict) -> dict:
    """Form values -> case record (round 0). Mimics the output of the extraction step and of Agent 1."""
    usable = not f["no_audio"]
    top = []
    if usable:
        rest = round((1 - f["prob"]) / 2, 2)
        others = [x for x in LABEL_NAME if x != f["label"]][:2]
        top = [{"label": f["label"], "prob": f["prob"]}] + [{"label": o, "prob": rest} for o in others]
    return {
        "case_id": "case_streamlit", "chat_id": "streamlit", "status": "audio_analysed", "round": 0,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user_input": {"text": f["text"], "voice_transcript": None, "language": "fr", "audio_file_id": None},
        "vehicle": {"make": f["make"] or None, "model": f["model"] or None, "year": f["year"] or None,
                    "fuel": f["fuel"] or None, "mileage_km": None},
        "symptom": {"description": f["symptom"], "context": f["context"], "missing_info": []},
        "audio": {
            "status": "ok" if usable else "unusable_audio", "label": f["label"] if usable else None,
            "context": f["context"] if f["context"] != "turning" else "unknown", "top3": top,
            "anomaly_score": f["anomaly"] if usable else 0.0, "confidence": f["confidence"] if usable else "low",
            "description": {"duration_s": 10.0, "dominant_freq_hz": f["freq"] or None, "periodic": f["periodic"],
                            "period_ms": None, "speech_ratio": 0.0, "summary": f["audio_summary"]},
            "model_version": "streamlit-manual", "warnings": [],
        },
    }


def call_agent(url: str, case: dict, timeout: int):
    """-> (result dict | None, error message | None)"""
    try:
        r = requests.post(url, data=json.dumps(case, ensure_ascii=False).encode("utf-8"),
                          headers={"Content-Type": "application/json; charset=utf-8"}, timeout=timeout)
    except requests.RequestException as e:
        return None, f"Impossible de joindre n8n ({e.__class__.__name__}). Docker et n8n sont-ils lancés ?"
    try:
        body = r.json()
    except ValueError:
        return None, f"Réponse non JSON (HTTP {r.status_code}): {r.text[:300]}"
    if r.status_code == 404:
        return None, "Webhook introuvable : le workflow n'est pas actif (interrupteur « Active » en haut à droite dans n8n)."
    if r.status_code >= 400 or body.get("message") == "Error in workflow":
        return None, ("Erreur dans le workflow n8n. Cause la plus fréquente : quota gratuit Gemini épuisé "
                      "(20 requêtes par jour et par modèle). Changez le modèle dans le noeud « Gemini Chat Model ».")
    return body, None


def reset():
    for k in ("case", "result", "history", "error", "answers_key"):
        st.session_state.pop(k, None)


def show_result(res: dict, round_: int):
    if res.get("insufficient"):
        st.warning(f"Pas de réponse fiable : {res.get('reason') or 'les manuels ne permettent pas de conclure'}. Les manuels fournis ne couvrent peut-être pas ce symptôme (essayez les cas « oil_pressure », « power_steering », « charge_light », « starter »). "
                   "Le système enverrait le message « informations insuffisantes ».")
        return
    if res.get("safety_stop"):
        st.error("⚠️ Ne roulez pas avec ce véhicule (cause la plus probable liée aux freins, à la direction ou à la surchauffe).")
    if res.get("message_to_user"):
        st.info(res["message_to_user"])
    st.subheader(f"Causes probables · tour {min(round_ + 1, MAX_ROUNDS)} / {MAX_ROUNDS}")
    for c in res["causes"]:
        left, right = st.columns([5, 1])
        left.markdown(f"**{c['name']}**" + (f"  ·  `{c['label']}`" if c.get("label") else ""))
        right.markdown(f"**{round(c['prob'] * 100)} %**")
        st.progress(min(max(c["prob"], 0.0), 1.0))
        with st.expander("Indices et sources"):
            for e in c["evidence"]:
                st.write("• " + e)
    if res["conclusive"]:
        st.success("Diagnostic conclusif : le dossier peut passer à l'Agent 3 (guide de réparation).")
    elif not res["checks"]:
        st.warning("Pas de conclusion nette et plus de vérifications possibles (limite de 2 tours) : "
                   "le système recommanderait de consulter un mécanicien.")


# ------------------------------------------------------------------ sidebar

with st.sidebar:
    st.header("Réglages")
    url = st.text_input("Webhook Agent 2 (n8n)", DEFAULT_URL)
    timeout = st.slider("Délai max (secondes)", 30, 400, 300)
    st.caption("Un appel dure de 40 secondes à 2 minutes et demie (modèle local + reranker sur CPU). Avec Gemini : 3 à 4 requêtes du quota gratuit.")
    st.divider()
    st.caption("Cas d'exemple")
    example = st.selectbox("Charger un cas", ["(formulaire vide)"] + [p.name for p in CASES])
    if st.button("Recommencer à zéro"):
        reset()
        st.rerun()

st.title("🔧 Agent 2 · diagnostic et vérifications")
st.caption("Page de test : elle joue le rôle de Telegram et de l'Agent 1, et envoie le dossier au workflow n8n.")

# ------------------------------------------------------------------ step 1: the case

defaults = {"make": "Renault", "model": "Clio", "year": 2015, "fuel": "diesel", "symptom": "grincement au démarrage à froid",
            "text": "Ma Clio 2015 diesel grince au démarrage à froid.", "context": "startup", "label": "belt", "prob": 0.78,
            "confidence": "medium", "anomaly": 0.81, "freq": 2600.0, "periodic": True,
            "audio_summary": "Sifflement aigu qui suit le régime du moteur.", "no_audio": False}
if example != "(formulaire vide)":
    ex = json.loads((ROOT / "agent2_diagnosis/tests/cases" / example).read_text("utf-8"))
    a, v = ex["audio"], ex["vehicle"]
    defaults.update(make=v.get("make") or "", model=v.get("model") or "", year=v.get("year") or 0, fuel=v.get("fuel") or "",
                    symptom=ex["symptom"]["description"], text=ex["user_input"]["text"], context=ex["symptom"]["context"],
                    label=a.get("label") or "belt", prob=(a["top3"][0]["prob"] if a.get("top3") else 0.5),
                    confidence=a["confidence"], anomaly=a["anomaly_score"], freq=a["description"].get("dominant_freq_hz") or 0.0,
                    periodic=bool(a["description"].get("periodic")), audio_summary=a["description"]["summary"],
                    no_audio=a["status"] != "ok")

started = "case" in st.session_state
with st.form("case_form", border=True):
    st.subheader("1. Le dossier")
    c1, c2, c3, c4 = st.columns(4)
    make = c1.text_input("Marque", defaults["make"])
    model = c2.text_input("Modèle", defaults["model"])
    year = c3.number_input("Année", 0, 2030, int(defaults["year"] or 0))
    fuel = c4.selectbox("Carburant", ["", "petrol", "diesel", "hybrid", "electric"],
                        index=["", "petrol", "diesel", "hybrid", "electric"].index(defaults["fuel"] or ""))
    text = st.text_input("Message de l'utilisateur", defaults["text"])
    s1, s2 = st.columns([3, 1])
    symptom = s1.text_input("Symptôme décrit", defaults["symptom"])
    context = s2.selectbox("Quand ?", CONTEXTS, index=CONTEXTS.index(defaults["context"]))
    st.markdown("**Analyse du son (ce que renverrait l'Agent 1)**")
    no_audio = st.checkbox("Pas d'audio exploitable", defaults["no_audio"])
    d1, d2, d3, d4 = st.columns(4)
    label = d1.selectbox("Type de bruit", list(LABEL_NAME), format_func=lambda x: f"{LABEL_NAME[x]} ({x})",
                         index=list(LABEL_NAME).index(defaults["label"]) if defaults["label"] in LABEL_NAME else 0)
    prob = d2.slider("Probabilité du type", 0.05, 0.99, float(defaults["prob"]))
    anomaly = d3.slider("Bruit inhabituel (percentile vs moteurs sains)", 0.0, 1.0, float(defaults["anomaly"]), help="0,5 = son typique d un moteur sain. Plus de 0,8 : inhabituel. Plus de 0,95 : très inhabituel.")
    confidence = d4.selectbox("Confiance", ["low", "medium", "high"], index=["low", "medium", "high"].index(defaults["confidence"]))
    e1, e2 = st.columns([1, 3])
    freq = e1.number_input("Fréquence dominante (Hz)", 0.0, 20000.0, float(defaults["freq"]))
    periodic = e1.checkbox("Impacts réguliers", defaults["periodic"])
    audio_summary = e2.text_area("Résumé du son", defaults["audio_summary"], height=100)
    go = st.form_submit_button("Lancer l'Agent 2", type="primary", disabled=started)

if go:
    case = build_case({"make": make, "model": model, "year": int(year), "fuel": fuel, "text": text, "symptom": symptom,
                       "context": context, "no_audio": no_audio, "label": label, "prob": prob, "anomaly": anomaly,
                       "confidence": confidence, "freq": freq, "periodic": periodic, "audio_summary": audio_summary})
    with st.spinner("L'agent cherche dans les manuels et réfléchit (jusqu'à 2 minutes et demie)..."):
        res, err = call_agent(url, case, timeout)
    st.session_state.error = err
    if res is not None:
        st.session_state.case, st.session_state.result, st.session_state.history = case, res, []
        st.rerun()

if st.session_state.get("error"):
    st.error(st.session_state.error)

# ------------------------------------------------------------------ step 2: results and answers

if "case" in st.session_state:
    case, res = st.session_state.case, st.session_state.result
    round_ = case["round"]
    st.divider()
    st.subheader("2. Réponse de l'Agent 2" + (f" (pass {res['pass']})" if "pass" in res else ""))
    show_result(res, round_)

    if res.get("checks") and not res.get("conclusive") and round_ < MAX_ROUNDS:
        st.subheader("Vérifications à faire")
        st.caption("Moteur coupé et frein à main serré avant toute vérification.")
        answers = {}
        for chk in res["checks"]:
            cit = chk["citation"]
            where = f"{cit['source']}" + (f", p. {cit['page']}" if cit.get("page") else "")
            st.markdown(f"**{chk['id']} · {chk['q']}**")
            st.caption(f"{chk['why']}  \nSource : {where}")
            key = f"ans_{round_}_{chk['id']}"
            if chk["answer_type"] == "choice" and chk.get("choices"):
                answers[chk["id"]] = st.radio(chk["id"], chk["choices"] + ["je ne sais pas"], key=key, horizontal=True,
                                              label_visibility="collapsed", index=None)
            elif chk["answer_type"] == "free_text":
                answers[chk["id"]] = st.text_input(chk["id"], key=key, label_visibility="collapsed")
            else:
                answers[chk["id"]] = st.radio(chk["id"], ANSWERS_YN, key=key, horizontal=True,
                                              label_visibility="collapsed", index=None)
        if st.button("Envoyer mes réponses", type="primary", disabled=not all(answers.values())):
            nxt = copy.deepcopy(case)
            nxt["round"] = round_ + 1
            nxt["status"] = "waiting_checks"
            nxt["diagnosis"] = case.get("diagnosis", []) + [res]
            nxt["check_answers"] = case.get("check_answers", []) + [
                {"check_id": k, "answer": v, "round": nxt["round"]} for k, v in answers.items()]
            with st.spinner("Nouvelle analyse avec vos réponses..."):
                new, err = call_agent(url, nxt, timeout)
            st.session_state.error = err
            if new is not None:
                st.session_state.history.append(res)
                st.session_state.case, st.session_state.result = nxt, new
            st.rerun()

    if st.session_state.history:
        with st.expander("Tours précédents"):
            for i, h in enumerate(st.session_state.history, 1):
                st.markdown(f"**Tour {i}** : " + ", ".join(f"{c['name']} {round(c['prob'] * 100)} %" for c in h.get("causes", [])))

    with st.expander("JSON brut (agent2_output)"):
        st.json(res)
    with st.expander("Dossier envoyé à n8n (case_record)"):
        st.json(case)
