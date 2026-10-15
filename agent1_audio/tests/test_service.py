import io
import json
import sys
import wave
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent1_audio"))
from contracts.validate import errors  # noqa: E402
from service.app import app  # noqa: E402

client = TestClient(app)


def wav_bytes(seconds, freq=2800):
    t = np.arange(int(16000 * seconds)) / 16000
    x = (0.3 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes(x.tobytes())
    return buf.getvalue()


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_analyze_returns_valid_contract():
    r = client.post("/analyze", files={"file": ("a.wav", wav_bytes(5), "audio/wav")},
                    data={"context": "idle"})
    assert r.status_code == 200
    out = r.json()
    assert errors("agent1_output", out) == []
    assert out["description"]["duration_s"] == 5.0


def test_too_short_is_unusable():
    r = client.post("/analyze", files={"file": ("a.wav", wav_bytes(1), "audio/wav")})
    out = r.json()
    assert out["status"] == "unusable_audio"
    assert errors("agent1_output", out) == []
