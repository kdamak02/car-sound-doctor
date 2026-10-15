"""
Car Sound Doctor - Agent 1 test bench (Streamlit).

    pip install streamlit requests
    streamlit run agent1_audio/demo/app.py          # from the repo root

Two modes (sidebar):
  * Direct model  : imports src/model.py (needs models/agent1_ast.joblib + agent1_ae.pt,
                    torch, transformers, librosa). Best on Lightning AI.
  * HTTP service  : calls POST /analyze exactly like n8n will (service can be mock or real).

What it shows: the sound, its spectrogram, the predicted fault (top 3), confidence, anomaly
score, the plain-fact description, the JSON contract check, and the text Agent 2 will receive
(prompt P3). Test-set examples show the true label, and "simulate Telegram" re-encodes the
audio through Opus like a voice note.
"""
import csv
import io
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
A1 = ROOT / "agent1_audio"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(A1))
from src.audio_utils import SR, describe, load_audio  # noqa: E402

DATA = ROOT / "data" / "audio" / "processed"
LABELS = json.loads((ROOT / "contracts" / "labels.json").read_text("utf-8"))
FR = {l["id"]: l["fr"] for l in LABELS["labels"]}
CONTEXTS = {"idle": "Au ralenti", "startup": "Au démarrage", "braking": "Au freinage",
            "driving": "En roulant", "unknown": "Je ne sais pas"}

st.set_page_config(page_title="Car Sound Doctor · Agent 1", page_icon="🔧", layout="wide")


# ------------------------------------------------------------------ helpers
def ffmpeg():
    import shutil
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def to_opus(path, bitrate):
    out = Path(path).with_suffix(".tg.ogg")
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-i", str(path), "-ac", "1",
                    "-ar", "48000", "-c:a", "libopus", "-b:a", bitrate, "-application", "voip",
                    str(out)], check=True)
    return out


@st.cache_resource(show_spinner="Chargement du modèle (AST + autoencodeur)…")
def direct_model():
    from src import model
    model._load()                      # warm up once
    return model


def analyze_direct(path, context):
    x = load_audio(path)
    desc = describe(x)
    if desc["duration_s"] < 2.0:
        return {"status": "unusable_audio", "label": None, "context": "unknown", "top3": [],
                "anomaly_score": 0.0, "confidence": "low", "description": desc,
                "model_version": "n/a", "warnings": ["Recording shorter than 2 seconds."]}
    out = direct_model().predict(x, context=context)
    out["description"] = {**desc, **out.get("description", {})}
    return out


def analyze_http(path, context, url):
    import requests
    with open(path, "rb") as f:
        r = requests.post(url.rstrip("/") + "/analyze", files={"file": (Path(path).name, f)},
                          data={"context": context}, timeout=60)
    r.raise_for_status()
    return r.json()


def p3_text(o):
    """What Agent 2 receives (prompt P3, see agent1_audio/INTEGRATION.md)."""
    if o["status"] != "ok":
        return "AUDIO ANALYSIS: the recording could not be used. Ask the user to record again."
    t = o["top3"]
    a = o["anomaly_score"]
    level = "very high" if a >= 0.95 else "high" if a >= 0.8 else "normal range"
    others = ", ".join(f"{FR.get(x['label'], x['label'])} ({x['prob'] * 100:.0f} %)" for x in t[1:])
    return (f"AUDIO ANALYSIS (automatic, may be wrong):\n"
            f"- Most likely sound class: {FR.get(o['label'], o['label'])} "
            f"(probability {t[0]['prob'] * 100:.0f} %, confidence {o['confidence']}).\n"
            f"- Other possibilities: {others or 'none'}.\n"
            f"- Unusualness compared with healthy engines: {level}.\n"
            f"- Measured facts: {o['description'].get('summary', '')}\n"
            f"Treat this as a hypothesis to verify with the user's checks, never as a diagnosis.\n"
            f"The audio model only knows a limited list of faults; other faults are possible.")


def spectrogram(path):
    import librosa
    import librosa.display
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    x = load_audio(path)
    m = librosa.power_to_db(librosa.feature.melspectrogram(y=x, sr=SR, n_mels=64), ref=np.max)
    fig, ax = plt.subplots(figsize=(8, 2.6))
    librosa.display.specshow(m, sr=SR, x_axis="time", y_axis="mel", ax=ax, cmap="magma")
    ax.set_title("Spectrogramme Mel (ce que « voit » le modèle)")
    fig.tight_layout()
    return fig


@st.cache_data
def test_examples():
    p = DATA / "metadata.csv"
    if not p.exists():
        return []
    return [r for r in csv.DictReader(open(p, encoding="utf-8")) if r["split"] == "test"]


# ------------------------------------------------------------------ sidebar
st.sidebar.header("Réglages")
mode = st.sidebar.radio("Moteur d'analyse", ["Modèle direct", "Service HTTP /analyze"],
                        help="Direct : charge le modèle dans cette page. HTTP : appelle le "
                             "service comme le fera n8n.")
url = st.sidebar.text_input("URL du service", "http://localhost:8001",
                            disabled=mode != "Service HTTP /analyze")
tg = st.sidebar.checkbox("Simuler une note vocale Telegram (Opus)", value=False)
br = st.sidebar.select_slider("Débit Opus", ["8k", "12k", "16k", "24k", "32k"], value="16k",
                              disabled=not tg)
st.sidebar.caption("Le modèle a été entraîné avec une augmentation Opus 12k-32k.")

# ------------------------------------------------------------------ input
st.title("🔧 Car Sound Doctor · banc de test de l'Agent 1")
st.caption("Son du moteur → panne probable, score d'anomalie et texte transmis à l'Agent 2.")

tab_up, tab_rec, tab_ex = st.tabs(["📁 Importer un fichier", "🎙️ Enregistrer", "🧪 Exemple du jeu de test"])
audio_bytes, name, truth = None, None, None
with tab_up:
    f = st.file_uploader("Fichier audio (WAV, OGG/Opus de Telegram, MP3, M4A)",
                         type=["wav", "ogg", "oga", "opus", "mp3", "m4a", "flac", "webm"])
    if f:
        audio_bytes, name = f.getvalue(), f.name
with tab_rec:
    if hasattr(st, "audio_input"):
        rec = st.audio_input("Enregistre 5 à 10 secondes près du moteur")
        if rec and audio_bytes is None:
            audio_bytes, name = rec.getvalue(), "micro.wav"
    else:
        st.info("Ta version de Streamlit ne gère pas l'enregistrement : `pip install -U streamlit`.")
with tab_ex:
    ex = test_examples()
    if not ex:
        st.info("Aucun exemple : `data/audio/processed/metadata.csv` est introuvable sur cette machine.")
    else:
        labs = sorted({r["label"] for r in ex})
        lab = st.selectbox("Panne réelle", labs, format_func=lambda l: f"{FR.get(l, l)} ({l})")
        files = [r for r in ex if r["label"] == lab]
        pick = st.selectbox("Clip", files, format_func=lambda r: f"{r['file'].split('/')[-1]} · {r['dataset']} · {r['duration_s']} s")
        if pick and audio_bytes is None and st.toggle("Utiliser cet exemple", value=True):
            audio_bytes, name, truth = (DATA / pick["file"]).read_bytes(), pick["file"].split("/")[-1], pick

default_ctx = next((l["context"] for l in LABELS["labels"]
                    if truth and l["id"] == truth["label"]), "idle")
ctx = st.radio("Quand le bruit apparaît-il ?", list(CONTEXTS), horizontal=True,
               index=list(CONTEXTS).index(default_ctx), format_func=CONTEXTS.get)
go = st.button("Analyser", type="primary", disabled=audio_bytes is None)

# ------------------------------------------------------------------ analysis
if go and audio_bytes:
    tmp = Path(tempfile.mkdtemp()) / (name or "audio.wav")
    tmp.write_bytes(audio_bytes)
    path = to_opus(tmp, br) if tg else tmp
    c1, c2 = st.columns([1, 1])
    with c1:
        st.audio(path.read_bytes())
        if tg:
            st.caption(f"Version compressée Opus {br} (ce que reçoit le bot).")
    with c2:
        st.pyplot(spectrogram(path))

    t0 = time.time()
    try:
        with st.spinner("Analyse…"):
            out = analyze_direct(path, ctx) if mode == "Modèle direct" else analyze_http(path, ctx, url)
    except Exception as e:
        st.error(f"L'analyse a échoué : {e}")
        st.stop()
    dt = time.time() - t0

    if out["status"] != "ok":
        st.warning("Enregistrement inutilisable : " + "; ".join(out.get("warnings", [])))
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Panne probable", FR.get(out["label"], out["label"]),
                  f"{out['top3'][0]['prob'] * 100:.0f} %")
        m2.metric("Confiance", {"high": "Élevée", "medium": "Moyenne", "low": "Faible"}[out["confidence"]])
        m3.metric("Anomalie", f"{out['anomaly_score']:.2f}",
                  "très inhabituel" if out["anomaly_score"] >= 0.95 else
                  "inhabituel" if out["anomaly_score"] >= 0.8 else "plage normale",
                  delta_color="off")
        m4.metric("Temps d'analyse", f"{dt:.1f} s")
        if truth:
            ok = truth["label"] == out["label"]
            (st.success if ok else st.error)(
                f"Vérité terrain : **{FR.get(truth['label'], truth['label'])}** → "
                f"{'prédiction correcte ✅' if ok else 'prédiction incorrecte ❌'}")
        import pandas as pd
        st.subheader("Top 3")
        st.bar_chart(pd.DataFrame({"probabilité": [t["prob"] for t in out["top3"]]},
                                  index=[FR.get(t["label"], t["label"]) for t in out["top3"]]))
        st.write("**Faits mesurés :** " + out["description"].get("summary", ""))
        for w in out.get("warnings", []):
            st.info(w)

    st.subheader("Texte transmis à l'Agent 2 (prompt P3)")
    st.code(p3_text(out), language="text")

    with st.expander("Réponse JSON brute et vérification du contrat"):
        st.json(out)
        try:
            from contracts.validate import errors
            errs = errors("agent1_output", out)
            if errs:
                st.error("Contrat non respecté :\n" + "\n".join(errs))
            else:
                st.success("Conforme à contracts/agent1_output.schema.json ✅")
        except Exception as e:
            st.info(f"Validation impossible ici ({e}). `pip install jsonschema`")
        st.caption(f"model_version : {out.get('model_version')}")
