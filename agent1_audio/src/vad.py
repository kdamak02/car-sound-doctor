"""Speech detection (Silero VAD, ONNX, MIT licence: vad/LICENSE_silero_vad.txt).

speech_ratio(x) = share of 32 ms frames where the VAD sees a human voice (0-1), or None when onnxruntime is not
installed (mock mode). Measured on 7 speech clips (CREMA-D + Silero sample) and 47 engine clips of our val/test
sets, clean and through Opus 16 kbit/s: speech 0.41-0.85, engines 0.00 (one YouTube clip with a voice-over: 0.24).
SPEECH_REJECT = 0.35 rejects a spoken message; SPEECH_WARN flags a recording where someone also talks.
"""
from pathlib import Path

import numpy as np

MODEL = Path(__file__).resolve().parents[1] / "vad" / "silero_vad.onnx"
SPEECH_REJECT = 0.35
SPEECH_WARN = 0.10
_sess = None


def _session():
    global _sess
    if _sess is None:
        import onnxruntime as ort  # optional dependency (requirements-real.txt)
        so = ort.SessionOptions()
        so.inter_op_num_threads = so.intra_op_num_threads = 1
        _sess = ort.InferenceSession(str(MODEL), sess_options=so, providers=["CPUExecutionProvider"])
    return _sess


def available():
    try:
        _session()
        return True
    except Exception:
        return False


def speech_ratio(x, sr=16000, threshold=0.5):
    """x: mono float32 in [-1, 1] at 16 kHz."""
    if sr != 16000 or not available():
        return None
    sess = _session()
    state = np.zeros((2, 1, 128), np.float32)
    ctx = np.zeros((1, 64), np.float32)
    srt = np.array(16000, np.int64)
    probs = []
    x = np.asarray(x, np.float32)
    for i in range(0, len(x) - 512 + 1, 512):
        chunk = x[i:i + 512][None, :]
        p, state = sess.run(None, {"input": np.concatenate([ctx, chunk], axis=1), "state": state, "sr": srt})
        ctx = chunk[:, -64:]
        probs.append(float(p[0, 0]))
    return round(float(np.mean(np.array(probs) > threshold)), 3) if probs else 0.0
