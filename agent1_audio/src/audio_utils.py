"""Shared audio helpers: decoding (any format, incl. Telegram OGG/Opus) and the
plain-fact description sent to the LLMs."""
import shutil
import subprocess

import numpy as np

SR = 16000
MIN_DURATION_S = 1.5   # one model window; shorter recordings are rejected


def ffmpeg_exe():
    """System ffmpeg, or the one shipped by `pip install imageio-ffmpeg` (e.g. Lightning AI)."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as e:
        raise RuntimeError("ffmpeg not found: install it or run  pip install imageio-ffmpeg") from e


def load_audio(path, sr=SR):
    """Decode any audio file to mono float32 in [-1, 1] at `sr` Hz, using ffmpeg."""
    r = subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-i", str(path), "-ac", "1",
                        "-ar", str(sr), "-f", "s16le", "-"], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace")[-200:])
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def describe(x, sr=SR):
    """Simple, explainable facts about the sound (no model needed)."""
    dur = len(x) / sr
    out = {"duration_s": round(dur, 2), "dominant_freq_hz": None, "periodic": None,
           "period_ms": None, "speech_ratio": 0.0, "summary": ""}
    if dur < 0.5:
        return out
    # dominant frequency from the average spectrum
    n = 2048
    frames = [x[i:i + n] * np.hanning(n) for i in range(0, len(x) - n, n // 2)]
    spec = np.mean([np.abs(np.fft.rfft(f)) for f in frames], axis=0)
    freqs = np.fft.rfftfreq(n, 1 / sr)
    band = freqs > 30
    out["dominant_freq_hz"] = round(float(freqs[band][np.argmax(spec[band])]), 1)
    # periodicity of the loudness envelope (ticks, knocks): autocorrelation peak 40-1000 ms
    hop = 160  # 10 ms
    env = np.array([np.sqrt(np.mean(x[i:i + hop] ** 2)) for i in range(0, len(x) - hop, hop)])
    env = env - env.mean()
    if np.any(env):
        ac = np.correlate(env, env, mode="full")[len(env) - 1:]
        ac = ac / ac[0]
        lo, hi = 4, min(100, len(ac) - 1)
        if hi > lo:
            k = lo + int(np.argmax(ac[lo:hi]))
            out["periodic"] = bool(ac[k] > 0.5)
            out["period_ms"] = k * 10 if out["periodic"] else None
    f = out["dominant_freq_hz"]
    pitch = "high-pitched" if f > 2000 else "mid-pitched" if f > 500 else "low-pitched"
    rhythm = f"regular impacts every {out['period_ms']} ms" if out["periodic"] else "no clear rhythm"
    out["summary"] = f"{dur:.1f} s recording, {pitch} sound (dominant {f:.0f} Hz), {rhythm}."
    return out
