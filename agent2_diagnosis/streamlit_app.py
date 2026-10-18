"""
Car Sound Doctor: test page for the whole chain Agent 1 (sound) -> Agent 2 (diagnosis) -> Agent 3 (repair guide). Owner: B2.

    pip install streamlit requests
    streamlit run agent2_diagnosis/streamlit_app.py        (or start everything with agent2_diagnosis/start_full_chain.ps1)

You describe the car and the problem and give a recording of the engine (upload one, or use the demo clip). The page sends them to the n8n workflow:
  - with a recording: POST /webhook/agent2-diagnose-audio  -> Agent 1 analyses the sound, then Agent 2, then Agent 3 (when the diagnosis is conclusive)
  - without: POST /webhook/agent2-diagnose                  -> Agent 2 from the text only
Pass 1 returns causes and checks; you answer the checks and send them back for pass 2 (max 2 rounds). Each agent has its own card with its own output.
Needs: n8n with ONE full-chain workflow active (n8n/workflows/full_chain_*.json), the RAG on :8002, Agent 1 on :8001, Agent 3 on :8003.
"""
import copy
import json
import mimetypes
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent2_diagnosis.labels import load_labels  # noqa: E402
from agent2_diagnosis.to_agent3 import to_agent3  # noqa: E402

LABELS = load_labels(ROOT)
LABEL_NAME = {x["id"]: x["fr"] for x in LABELS["labels"]}
CONTEXTS = {"startup": "Au démarrage", "idle": "Au ralenti", "driving": "En roulant", "braking": "Au freinage", "unknown": "Je ne sais pas"}
CASES = sorted((ROOT / "agent2_diagnosis/tests/cases").glob("*.json"))
DEMO_CLIP = ROOT / "data/audio/demo_clips/demo_failed_start.ogg"
MAX_ROUNDS = 2
DEFAULT_URL = os.getenv("AGENT2_WEBHOOK", "http://localhost:5678/webhook/agent2-diagnose")
AGENT3_URL = os.getenv("AGENT3_URL", "http://127.0.0.1:8003/generate")  # only for the "ask Agent 3 now" button
ANSWERS_YN = ["oui", "non", "je ne sais pas"]

st.set_page_config(page_title="Car Sound Doctor - test", page_icon="🚗", layout="wide")


# ------------------------------------------------------------------ helpers

def build_case(f: dict) -> dict:
    """Form values -> case record (round 0). No `audio`: Agent 1 produces it from the recording."""
    return {
        "case_id": "case_streamlit", "chat_id": "streamlit", "status": "collecting_info", "round": 0,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user_input": {"text": f["text"], "voice_transcript": None, "language": "fr", "audio_file_id": None},
        "vehicle": {"make": f["make"] or None, "model": f["model"] or None, "year": f["year"] or None,
                    "fuel": f["fuel"] or None, "mileage_km": None},
        "symptom": {"description": f["symptom"], "context": f["context"], "missing_info": []},
    }


def call_agent(url: str, case: dict, timeout: int, audio=None):
    """-> (result dict | None, error message | None). `audio` = (file name, bytes): the voice note goes to the audio entry of the workflow
    (Agent 1, then Agent 2, then Agent 3); the reply is {agent1, agent2, agent3}."""
    try:
        if audio:
            r = requests.post(url.replace("agent2-diagnose", "agent2-diagnose-audio"), timeout=timeout,
                              files={"audio": (audio[0], audio[1], mimetypes.guess_type(audio[0])[0] or "application/octet-stream")},
                              data={"case": json.dumps(case, ensure_ascii=False)})
        else:
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
        return None, ("Erreur dans le workflow n8n. Causes fréquentes : quota du modèle épuisé (Groq : 200 000 tokens par jour et par modèle ; "
                      "Gemini : 20 requêtes par jour) ou un service arrêté. Ouvrez l'exécution dans n8n pour voir le détail.")
    return body, None


def unwrap(body: dict):
    """Workflow reply -> (agent1, agent2 output, agent3, needs_new_audio message). A plain agent2_output is accepted too."""
    if body.get("needs_new_audio"):
        return None, None, None, body.get("message") or "Enregistrement inutilisable."
    if "agent2" in body:
        return body.get("agent1"), body["agent2"], body.get("agent3"), None
    return None, body, None, None


def reset():
    for k in ("case", "result", "history", "error", "agent1", "agent3", "new_audio", "elapsed", "used_audio"):
        st.session_state.pop(k, None)


def pct(p) -> str:
    return f"{round(p * 100)} %"


# ------------------------------------------------------------------ the three pipeline cards

def pipeline(started: bool, a1, res, a3, used_audio: bool):
    """One line per agent at the top: what it is, and where it stands."""
    if not started:
        s1 = s2 = s3 = "⏳ en attente"
    else:
        if a1 and a1.get("status") == "ok":
            s1 = f"✅ **{LABEL_NAME.get(a1['label'], a1['label'])}** · confiance {a1['confidence']}"
        elif used_audio:
            s1 = "⚠️ pas d'analyse : l'Agent 2 travaille sur le texte seul"
        else:
            s1 = "➖ pas d'enregistrement"
        if res and res.get("insufficient"):
            s2 = "⚠️ pas de réponse fiable (les manuels ne couvrent pas ce cas)"
        elif res and res.get("causes"):
            s2 = f"✅ tour {res.get('pass', '?')} : **{res['causes'][0]['name'][:42]}** {pct(res['causes'][0]['prob'])}"
            if res.get("checks") and not res.get("conclusive"):
                s2 += " · vérifications à faire"
        else:
            s2 = "⏳ en attente"
        guide = (a3 or {}).get("guide")
        if guide:
            s3 = f"✅ guide **{guide.get('status')}**"
        elif res and res.get("causes") and (res.get("conclusive") or not res.get("checks")):
            s3 = "⚠️ pas de guide reçu"
        else:
            s3 = "⏳ attend un diagnostic conclusif"
    cols = st.columns(3)
    for col, (icon, name, role, status) in zip(cols, [
            ("🎧", "Agent 1", "Analyse du son", s1), ("🩺", "Agent 2", "Diagnostic et vérifications", s2), ("📖", "Agent 3", "Guide de réparation", s3)]):
        with col.container(border=True):
            st.markdown(f"#### {icon} {name}")
            st.caption(role)
            st.markdown(status)


# ------------------------------------------------------------------ the three agent sections

def show_agent1(a1, used_audio: bool):
    st.markdown("### 🎧 Agent 1 · Analyse du son")
    st.caption("Reçoit : l'enregistrement et le moment où le bruit apparaît. Produit : la classe de bruit, la confiance et le caractère inhabituel.")
    if not a1 or a1.get("status") != "ok":
        st.warning("Pas d'analyse du son" + (" (service arrêté, enregistrement trop court ou illisible)" if used_audio else " (aucun enregistrement envoyé)")
                   + " : l'Agent 2 travaille sur la description écrite seule.")
        return
    left, right = st.columns([1, 2])
    left.metric("Classe de bruit", LABEL_NAME.get(a1["label"], a1["label"]))
    left.metric("Confiance", a1["confidence"])
    left.metric("Bruit inhabituel (0,5 = moteur sain)", a1["anomaly_score"])
    for t in a1.get("top3", []):
        right.markdown(f"{LABEL_NAME.get(t['label'], t['label'])} · {pct(t['prob'])}")
        right.progress(min(max(t["prob"], 0.0), 1.0))
    right.caption(a1.get("description", {}).get("summary", ""))
    if a1.get("warnings"):
        st.caption("Avertissements : " + "; ".join(map(str, a1["warnings"])))
    with st.expander("Sortie brute de l'Agent 1 (JSON)"):
        st.json(a1)


def show_agent2(res: dict, round_: int, case: dict):
    st.markdown("### 🩺 Agent 2 · Diagnostic et vérifications" + (f" (passe {res['pass']})" if "pass" in res else ""))
    st.caption("Reçoit : le dossier, l'analyse du son et vos réponses. Cherche dans les manuels du constructeur. Produit : des causes classées, des conseils et des vérifications.")
    if res.get("insufficient"):
        st.warning(f"Pas de réponse fiable : {res.get('reason') or 'les manuels ne permettent pas de conclure'}. "
                   "Les manuels fournis ne couvrent peut-être pas ce symptôme : l'Agent 2 préfère ne rien inventer "
                   "(essayez les cas « oil_pressure », « power_steering », « charge_light », « starter »).")
        return
    if res.get("safety_stop"):
        st.error("⚠️ Ne roulez pas avec ce véhicule (cause la plus probable liée aux freins, à la direction ou à la surchauffe).")
    if res.get("message_to_user"):
        st.info(res["message_to_user"])
    st.markdown(f"**Causes probables · tour {min(round_ + 1, MAX_ROUNDS)} / {MAX_ROUNDS}**")
    for c in res["causes"]:
        left, right = st.columns([5, 1])
        left.markdown(f"{c['name']}" + (f"  ·  `{c['label']}`" if c.get("label") else ""))
        right.markdown(f"**{pct(c['prob'])}**")
        st.progress(min(max(c["prob"], 0.0), 1.0))
        with st.expander("Indices et sources"):
            for e in c["evidence"]:
                st.write("• " + e)
    if res["conclusive"]:
        st.success("Diagnostic conclusif : le dossier part vers l'Agent 3.")
    elif not res["checks"]:
        st.warning("Pas de conclusion nette et plus de vérifications possibles (limite de 2 tours) : le système recommanderait de consulter un mécanicien.")


def ask_agent3_now(case: dict, res: dict, url: str):
    """Test aid: send the CURRENT diagnosis (even if not conclusive yet) straight to Agent 3 and keep its answer.
    In the real flow n8n only calls Agent 3 when the diagnosis is conclusive or the rounds are over."""
    try:
        r = requests.post(url, json={"case_record": to_agent3(case, res), "retrieve": True}, timeout=180)
        body = r.json()
    except (requests.RequestException, ValueError) as e:
        return {"error": f"Agent 3 injoignable ({e.__class__.__name__}). Est-il lancé sur le port 8003 ?"}
    if r.status_code != 200:
        return {"error": f"Agent 3 : HTTP {r.status_code} {str(body)[:200]}"}
    return body


def show_agent3(a3, res):
    st.markdown("### 📖 Agent 3 · Guide de réparation")
    st.caption("Reçoit : le diagnostic conclusif de l'Agent 2, l'analyse du son et vos réponses. Cherche dans ses procédures de réparation. Produit : un guide cité et validé.")
    if not a3:
        if res and not res.get("insufficient") and res.get("checks") and not res.get("conclusive"):
            st.info("Pas encore : l'Agent 3 reçoit le dossier quand le diagnostic est conclusif ou après le dernier tour. Répondez d'abord aux vérifications, "
                    "ou demandez-lui un guide tout de suite à partir du diagnostic provisoire (test).")
            if st.button("Demander le guide à l'Agent 3 maintenant (diagnostic provisoire)"):
                st.session_state.agent3 = ask_agent3_now(st.session_state.case, res, AGENT3_URL)
                st.rerun()
        else:
            st.info("Pas de guide : aucun diagnostic exploitable n'a été transmis à l'Agent 3.")
        return
    guide = a3.get("guide")
    if not guide:
        st.warning("L'Agent 3 n'a pas répondu : " + str(a3.get("error") or a3.get("message") or a3)[:300])
        return
    status = guide.get("status")
    (st.success if status == "guide_available" else st.warning)(f"Statut : **{status}** · panne retenue : {(guide.get('fault') or {}).get('name', '?')}")
    for w in guide.get("safety_warnings") or []:
        st.error("⚠️ " + str(w))
    for stp in (guide.get("repair_guide") or {}).get("steps") or []:
        st.markdown(f"**{stp.get('number')}.** {stp.get('action')}  \n*source : {stp.get('source_id')}"
                    + (f", p. {stp['page']}" if stp.get("page") else "") + "*")
    for lim in guide.get("limitations") or []:
        st.caption("Limite : " + str(lim))
    val = a3.get("validation") or {}
    st.caption(f"Validation de l'Agent 3 : {'OK' if val.get('valid') else 'à vérifier'} · citations {'OK' if val.get('citations_valid') else '?'}"
               f" · sécurité {'OK' if val.get('safety_passed') else '?'}")
    with st.expander("Sortie brute de l'Agent 3 (JSON)"):
        st.json(a3)


# ------------------------------------------------------------------ sidebar

with st.sidebar:
    st.header("Réglages")
    url = st.text_input("Webhook n8n", DEFAULT_URL)
    timeout = st.slider("Délai max (secondes)", 30, 400, 300)
    st.caption("Une analyse complète dure de 10 secondes (Groq) à 3 minutes (modèle local). Les modèles gratuits ont un quota journalier.")
    st.divider()
    st.caption("Remplir le formulaire avec un cas d'exemple (voiture et problème)")
    example = st.selectbox("Cas d'exemple", ["(cas par défaut : Clio qui ne démarre pas)"] + [p.name for p in CASES])
    if st.button("Recommencer à zéro"):
        reset()
        st.rerun()

st.title("🚗 Car Sound Doctor")
st.caption("Test des 3 agents ensemble : 🎧 Agent 1 écoute le moteur → 🩺 Agent 2 diagnostique avec les manuels → 📖 Agent 3 propose le guide de réparation.")

defaults = {"make": "Renault", "model": "Clio", "year": 2015, "fuel": "diesel", "context": "startup",
            "symptom": "le démarreur ne tourne pas, j'entends seulement un clic, le voyant de batterie est allumé",
            "text": "Ma Clio 2015 diesel ne démarre pas ce matin, j'entends juste un clic."}
if not example.startswith("("):
    ex = json.loads((ROOT / "agent2_diagnosis/tests/cases" / example).read_text("utf-8"))
    v = ex["vehicle"]
    defaults.update(make=v.get("make") or "", model=v.get("model") or "", year=v.get("year") or 0, fuel=v.get("fuel") or "",
                    symptom=ex["symptom"]["description"], text=ex["user_input"]["text"],
                    context=ex["symptom"]["context"] if ex["symptom"]["context"] in CONTEXTS else "unknown")

started = "case" in st.session_state
pipeline(started, st.session_state.get("agent1"), st.session_state.get("result"), st.session_state.get("agent3"), st.session_state.get("used_audio", False))

# ------------------------------------------------------------------ step 1: the case and the recording

with st.form("case_form", border=True):
    st.markdown("### 1 · Le dossier")
    c1, c2, c3, c4 = st.columns(4)
    make = c1.text_input("Marque", defaults["make"])
    model = c2.text_input("Modèle", defaults["model"])
    year = c3.number_input("Année", 0, 2030, int(defaults["year"] or 0))
    fuel = c4.selectbox("Carburant", ["", "petrol", "diesel", "hybrid", "electric"],
                        index=["", "petrol", "diesel", "hybrid", "electric"].index(defaults["fuel"] or ""))
    text = st.text_input("Message de l'utilisateur", defaults["text"])
    s1, s2 = st.columns([3, 1])
    symptom = s1.text_input("Problème décrit en une phrase", defaults["symptom"])
    context = s2.selectbox("Quand le bruit apparaît-il ?", list(CONTEXTS), format_func=CONTEXTS.get, index=list(CONTEXTS).index(defaults["context"]))
    st.markdown("**Enregistrement du moteur** (l'Agent 1 l'analyse pour de vrai)")
    r1, r2 = st.columns([2, 1])
    up = r1.file_uploader("Envoyer une note vocale (OGG, WAV, MP3, M4A)", type=["ogg", "oga", "opus", "wav", "mp3", "m4a"])
    use_demo = r2.checkbox("Utiliser le clip de démonstration", value=DEMO_CLIP.exists(), disabled=not DEMO_CLIP.exists(),
                           help="data/audio/demo_clips/demo_failed_start.ogg : 1,5 s d'un démarrage raté. Sans fichier ni clip, seul le texte est envoyé.")
    go = st.form_submit_button("Lancer les 3 agents", type="primary", disabled=started)

if go:
    case = build_case({"make": make, "model": model, "year": int(year), "fuel": fuel, "text": text, "symptom": symptom, "context": context})
    audio = (up.name, up.getvalue()) if up is not None else ((DEMO_CLIP.name, DEMO_CLIP.read_bytes()) if (use_demo and DEMO_CLIP.exists()) else None)
    t0 = time.time()
    with st.spinner("🎧 Agent 1 écoute… 🩺 Agent 2 cherche dans les manuels… 📖 Agent 3 prépare le guide… (de 10 secondes à 3 minutes)"):
        body, err = call_agent(url, case, timeout, audio)
    a1, res, a3, again = unwrap(body) if body is not None else (None, None, None, None)
    st.session_state.error = err
    st.session_state.new_audio = again
    if res is not None:
        if a1:
            case["audio"] = a1  # pass 2 sends the real Agent 1 analysis back
        st.session_state.case, st.session_state.result, st.session_state.history = case, res, []
        st.session_state.agent1, st.session_state.agent3 = a1, a3
        st.session_state.used_audio, st.session_state.elapsed = audio is not None, round(time.time() - t0)
        st.rerun()

if st.session_state.get("error"):
    st.error(st.session_state.error)
if st.session_state.get("new_audio"):
    st.warning(st.session_state.new_audio)

# ------------------------------------------------------------------ step 2: what each agent produced

if "case" in st.session_state:
    case, res = st.session_state.case, st.session_state.result
    round_ = case["round"]
    st.caption(f"Dernier appel : {st.session_state.get('elapsed', '?')} s.")

    with st.container(border=True):
        show_agent1(st.session_state.get("agent1"), st.session_state.get("used_audio", False))
    with st.container(border=True):
        show_agent2(res, round_, case)

        if res.get("checks") and not res.get("conclusive") and round_ < MAX_ROUNDS:
            st.markdown("**Vérifications à faire**")
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
                t0 = time.time()
                with st.spinner("🩺 Agent 2 met à jour le diagnostic… 📖 Agent 3 prépare le guide…"):
                    body2, err = call_agent(url, nxt, timeout)
                st.session_state.error = err
                _, new, a3, _ = unwrap(body2) if body2 is not None else (None, None, None, None)
                if new is not None:
                    st.session_state.history.append(res)
                    st.session_state.case, st.session_state.result, st.session_state.agent3 = nxt, new, a3
                    st.session_state.elapsed = round(time.time() - t0)
                st.rerun()

        if st.session_state.history:
            with st.expander("Tours précédents"):
                for i, h in enumerate(st.session_state.history, 1):
                    st.markdown(f"**Tour {i}** : " + ", ".join(f"{c['name']} {pct(c['prob'])}" for c in h.get("causes", [])))
        with st.expander("Sortie brute de l'Agent 2 (JSON)"):
            st.json(res)
    with st.container(border=True):
        show_agent3(st.session_state.get("agent3"), res)
        if st.session_state.get("agent3"):
            with st.expander("Ce que l'Agent 2 a transmis à l'Agent 3 (dossier au format de l'Agent 3)"):
                try:
                    st.json(to_agent3(case, res))
                except (KeyError, TypeError, ValueError) as e:
                    st.caption(f"Dossier non convertible : {e}")
    with st.expander("Dossier envoyé à n8n (case_record)"):
        st.json(case)
