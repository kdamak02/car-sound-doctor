"""
Agent 1 audio service.

    POST /analyze   multipart file=<audio>, optional form field context=idle|startup|braking|driving
    GET  /health

MOCK_MODE=true  -> returns contracts/examples/agent1_output.json (with the real duration).
MOCK_MODE=false -> runs src/model.py:predict().

Run locally:  uvicorn service.app:app --port 8001 --reload   (from agent1_audio/)
"""
import json
import os
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from src.audio_utils import MIN_DURATION_S, describe, load_audio  # noqa: E402
from src import vad  # noqa: E402

CONTRACTS = Path(os.getenv("CONTRACTS_DIR", BASE.parent / "contracts"))
if not CONTRACTS.exists():                      # inside docker
    CONTRACTS = Path("/app/contracts")
MOCK = os.getenv("MOCK_MODE", "true").lower() == "true"
MAX_BYTES = 20 * 1024 * 1024

STATE = {"ready": MOCK, "warmup_s": None}


@asynccontextmanager
async def lifespan(_app):
    """Real mode: load AST + autoencoder + VAD and run one analysis BEFORE accepting requests, so the first real
    call takes seconds instead of 50-100 s (n8n gives up after 90 s). /health answers once this is done."""
    if not MOCK:
        t0 = time.time()
        from src.model import predict
        x = (0.01 * np.random.default_rng(0).standard_normal(16000 * 3)).astype(np.float32)
        predict(x, context="idle")
        vad.speech_ratio(x)
        STATE.update(ready=True, warmup_s=round(time.time() - t0, 1))
        print(f"[agent1] warm-up done in {STATE['warmup_s']} s (VAD {'on' if vad.available() else 'off'})", flush=True)
    yield


app = FastAPI(title="Car Sound Doctor - Agent 1 audio service", version="0.2.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "mock": MOCK, "ready": STATE["ready"], "warmup_s": STATE["warmup_s"],
            "speech_detection": vad.available()}


@app.post("/analyze")
async def analyze(file: UploadFile = File(...), context: str = Form("unknown")):
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "file larger than 20 MB")

    suffix = Path(file.filename or "audio.ogg").suffix or ".ogg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        path = tmp.name
    try:
        try:
            x = load_audio(path)
        except Exception as e:
            raise HTTPException(422, f"cannot decode audio: {e}")
        desc = describe(x)

        if desc["duration_s"] < MIN_DURATION_S:
            return unusable(desc, f"Recording shorter than {MIN_DURATION_S} seconds.")

        speech = vad.speech_ratio(x)          # None when onnxruntime is not installed (mock mode)
        if speech is not None:
            desc["speech_ratio"] = speech
            if speech >= vad.SPEECH_REJECT:
                return unusable(desc, f"Speech detected in {speech:.0%} of the recording: record the engine "
                                      "for 5-10 s without talking.")
        extra = []
        if speech is not None and speech >= vad.SPEECH_WARN:
            extra.append(f"Someone talks in {speech:.0%} of the recording; the analysis may be less reliable.")

        if MOCK:
            out = json.loads((CONTRACTS / "examples" / "agent1_output.json").read_text("utf-8"))
            out["description"]["duration_s"] = desc["duration_s"]
            out["context"] = context if context in ("idle", "startup", "braking", "driving") else "unknown"
            out["warnings"] = out.get("warnings", []) + extra
            return out

        from src.model import predict  # loaded lazily so mock mode needs no torch
        out = predict(x, context=context)
        out["description"] = {**out.get("description", {}), **desc}
        out["warnings"] = out.get("warnings", []) + extra
        return out
    finally:
        os.unlink(path)


def unusable(desc, reason):
    return {"status": "unusable_audio", "label": None, "context": "unknown", "top3": [],
            "anomaly_score": 0.0, "confidence": "low", "description": desc,
            "model_version": "n/a", "warnings": [reason]}
