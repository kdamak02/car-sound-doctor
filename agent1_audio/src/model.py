"""Real model used by the service when MOCK_MODE=false (owner A2).

Loads models/agent1_ast.joblib (written by src/ast_train.py) and the AST encoder.
Returns a dict valid against contracts/agent1_output.schema.json; the service merges
`description` with audio_utils.describe().
"""
import os
from pathlib import Path

import numpy as np

MODEL_PATH = Path(os.getenv("AGENT1_MODEL", Path(__file__).resolve().parents[2] / "models"
                            / "agent1_ast.joblib"))
if not MODEL_PATH.exists() and Path("/app/models/agent1_ast.joblib").exists():
    MODEL_PATH = Path("/app/models/agent1_ast.joblib")
_STATE = {}


def _load():
    if not _STATE:
        import joblib
        from src.ast_train import Embedder
        _STATE["m"] = joblib.load(MODEL_PATH)
        _STATE["emb"] = Embedder()
    return _STATE["m"], _STATE["emb"]


def predict(x, context="unknown"):
    """x: mono float32 waveform at 16 kHz."""
    from src.ast_train import windows
    m, emb = _load()
    E = emb(windows(x))
    p = m["clf"].predict_proba(E).mean(0)
    classes = np.array(m["classes"])
    if context in ("idle", "startup", "braking"):           # restrict to that context
        mask = np.array([m["ctx_of"][c] == context for c in classes])
        if mask.any():
            p = np.where(mask, p, 0.0)
            p = p / p.sum()
    order = np.argsort(p)[::-1]
    raw = float(np.mean(-m["iso"].score_samples(m["iso_scaler"].transform(E))))
    anomaly = float(np.searchsorted(m["iso_ref"], raw) / len(m["iso_ref"]))  # percentile
    top = float(p[order[0]])
    margin = top - float(p[order[1]]) if len(order) > 1 else top
    confidence = "high" if top >= 0.7 and margin >= 0.3 else "medium" if top >= 0.45 else "low"
    return {
        "status": "ok",
        "label": str(classes[order[0]]),
        "context": context if context in ("idle", "startup", "braking", "driving") else "unknown",
        "top3": [{"label": str(classes[i]), "prob": round(float(p[i]), 3)} for i in order[:3]],
        "anomaly_score": round(anomaly, 3),
        "confidence": confidence,
        "description": {},
        "model_version": m.get("version", "ast"),
        "warnings": [] if confidence != "low" else ["Low confidence: rely on the checks."],
    }
