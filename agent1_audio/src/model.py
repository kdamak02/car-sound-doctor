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
AE_PATH = MODEL_PATH.with_name("agent1_ae.pt")
_STATE = {}


def _load():
    if not _STATE:
        import joblib
        from src.ast_train import Embedder
        _STATE["m"] = joblib.load(MODEL_PATH)
        _STATE["emb"] = Embedder()
        _STATE["ae"] = None
        if AE_PATH.exists():                 # autoencoder = best anomaly detector (AUC 0.865)
            import torch
            from src.ae_anomaly import ConvAE
            d = torch.load(AE_PATH, map_location="cpu")
            ae = ConvAE()
            ae.load_state_dict(d["state"])
            ae.eval()
            ref = d.get("healthy_val_err", d["healthy_train_err"]).numpy()
            _STATE["ae"] = (ae, d["mu"], d["sd"], ref)
    return _STATE["m"], _STATE["emb"], _STATE["ae"]


def _ae_anomaly(x, ae_pack):
    """Mean reconstruction error as a percentile of healthy validation errors (0..1):
    0.5 = typical healthy sound, > 0.95 = louder error than almost every healthy clip."""
    import torch
    from src.ae_anomaly import logmel, windows
    ae, mu, sd, ref = ae_pack
    M = np.stack([logmel(w) for w in windows(x)])
    with torch.no_grad():
        xb = torch.from_numpy((M - mu) / sd).unsqueeze(1)
        err = float(((ae(xb) - xb) ** 2).mean())
    return float(np.searchsorted(ref, err) / len(ref))


def predict(x, context="unknown"):
    """x: mono float32 waveform at 16 kHz."""
    from src.ast_train import windows
    m, emb, ae_pack = _load()
    E = emb(windows(x))
    p = m["clf"].predict_proba(E).mean(0)
    classes = np.array(m["classes"])
    if context in ("idle", "startup", "braking", "driving"):  # restrict to that context
        mask = np.array([m["ctx_of"][c] == context for c in classes])
        if mask.any():
            p = np.where(mask, p, 0.0)
            p = p / p.sum()
    order = np.argsort(p)[::-1]
    if ae_pack is not None:
        anomaly, source = _ae_anomaly(x, ae_pack), "autoencoder"
    else:                                                    # fallback: Isolation Forest on AST
        raw = float(np.mean(-m["iso"].score_samples(m["iso_scaler"].transform(E))))
        anomaly, source = float(np.searchsorted(m["iso_ref"], raw) / len(m["iso_ref"])), "iforest"
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
        "model_version": f"{m.get('version', 'ast')}+anomaly:{source}",
        "warnings": ([] if confidence != "low" else ["Low confidence: rely on the checks."])
                    + ([] if context in ("idle", "startup", "braking", "driving")
                       else ["Context unknown: all classes considered."]),
    }
