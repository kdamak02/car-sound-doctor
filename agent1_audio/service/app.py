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
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from src.audio_utils import MIN_DURATION_S, describe, load_audio  # noqa: E402

CONTRACTS = Path(os.getenv("CONTRACTS_DIR", BASE.parent / "contracts"))
if not CONTRACTS.exists():                      # inside docker
    CONTRACTS = Path("/app/contracts")
MOCK = os.getenv("MOCK_MODE", "true").lower() == "true"
MAX_BYTES = 20 * 1024 * 1024

app = FastAPI(title="Car Sound Doctor - Agent 1 audio service", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok", "mock": MOCK}


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

        if MOCK:
            out = json.loads((CONTRACTS / "examples" / "agent1_output.json").read_text("utf-8"))
            out["description"]["duration_s"] = desc["duration_s"]
            out["context"] = context if context in ("idle", "startup", "braking", "driving") else "unknown"
            return out

        from src.model import predict  # loaded lazily so mock mode needs no torch
        out = predict(x, context=context)
        out["description"] = {**desc, **out.get("description", {})}
        return out
    finally:
        os.unlink(path)


def unusable(desc, reason):
    return {"status": "unusable_audio", "label": None, "context": "unknown", "top3": [],
            "anomaly_score": 0.0, "confidence": "low", "description": desc,
            "model_version": "n/a", "warnings": [reason]}
