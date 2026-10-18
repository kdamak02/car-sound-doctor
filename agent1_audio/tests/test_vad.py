"""Speech detection: an engine-like sound must not be taken for a voice (skipped without onnxruntime, e.g. in CI)."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import vad  # noqa: E402

pytestmark = pytest.mark.skipif(not vad.available(), reason="onnxruntime not installed")


def test_engine_like_sound_is_not_speech():
    t = np.arange(16000 * 4) / 16000
    rng = np.random.default_rng(0)
    x = (0.2 * np.sin(2 * np.pi * 55 * t) + 0.05 * rng.standard_normal(t.size)).astype(np.float32)
    assert vad.speech_ratio(x) < vad.SPEECH_REJECT


def test_silence_is_not_speech():
    assert vad.speech_ratio(np.zeros(16000 * 2, np.float32)) == 0.0
