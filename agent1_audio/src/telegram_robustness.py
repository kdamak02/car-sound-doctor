"""
Agent 1 - robustness to Telegram voice-note compression (owners A3 / A2).

Telegram voice notes are OGG/Opus, mono, low bitrate. Our training clips are clean WAV.
This script re-encodes every TEST clip through Opus at several bitrates, decodes it back,
and measures how much the trained AST model loses (domain shift).

    python agent1_audio/src/telegram_robustness.py               # 32k, 16k, 8k (~10 min CPU)
    python agent1_audio/src/telegram_robustness.py --bitrates 16k

Needs models/agent1_ast.joblib (from ast_train.py) and ffmpeg with libopus
(falls back to the ffmpeg shipped by `pip install imageio-ffmpeg`).
Output: agent1_audio/results/telegram_robustness.json
"""
import argparse
import csv
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ast_train import (DATA, MODELS, RES, SR, Embedder, clip_probs, label_context,  # noqa: E402
                       predict, read_wav, windows)


def ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def through_opus(path, bitrate, ff, tmp):
    """WAV -> OGG/Opus (like a Telegram voice note) -> 16 kHz mono float."""
    ogg = Path(tmp) / "x.ogg"
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", "48000",
                    "-c:a", "libopus", "-b:a", bitrate, "-application", "voip", str(ogg)],
                   check=True)
    r = subprocess.run([ff, "-loglevel", "error", "-i", str(ogg), "-ac", "1", "-ar", str(SR),
                        "-f", "s16le", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bitrates", default="32k,16k,8k")
    a = ap.parse_args()
    import joblib
    from sklearn.metrics import accuracy_score, f1_score

    m = joblib.load(MODELS / "agent1_ast.joblib")
    clf, classes = m["clf"], np.array(m["classes"])
    ctx_of = label_context()
    rows = [r for r in csv.DictReader(open(DATA / "metadata.csv", encoding="utf-8"))
            if r["split"] == "test" and r["label"] in set(classes)]
    y = np.array([r["label"] for r in rows])
    ctx = np.array([ctx_of[l] for l in y])
    emb, ff = Embedder(), ffmpeg_exe()
    print(f"{len(rows)} test clips, AST on {emb.device}, ffmpeg {ff}")

    def evaluate(get_audio, name):
        X, cid = [], []
        for i, r in enumerate(rows):
            for e in emb(windows(get_audio(r))):
                X.append(e)
                cid.append(i)
            if (i + 1) % 100 == 0:
                print(f"  [{name}] {i + 1}/{len(rows)}")
        X, cid = np.array(X), np.array(cid)
        P = clip_probs(clf, X, cid, range(len(rows)))
        p, pc = predict(P, classes), predict(P, classes, ctx, ctx_of)
        return {"macro_f1": round(f1_score(y, p, average="macro"), 3),
                "accuracy": round(accuracy_score(y, p), 3),
                "macro_f1_with_context": round(f1_score(y, pc, average="macro"), 3),
                "per_class_f1": {c: round(v, 2) for c, v in zip(
                    classes, f1_score(y, p, labels=classes, average=None, zero_division=0))}}

    res = {"clean WAV": evaluate(lambda r: read_wav(DATA / r["file"]), "clean")}
    with tempfile.TemporaryDirectory() as tmp:
        for br in a.bitrates.split(","):
            res[f"Opus {br}"] = evaluate(lambda r: through_opus(DATA / r["file"], br, ff, tmp),
                                         f"opus {br}")
    (RES / "telegram_robustness.json").write_text(json.dumps(res, indent=2), encoding="utf-8")

    print(f"\n{'audio':12s} {'macro-F1':>9s} {'accuracy':>9s} {'+context':>9s}")
    for k, v in res.items():
        print(f"{k:12s} {v['macro_f1']:9.3f} {v['accuracy']:9.3f} {v['macro_f1_with_context']:9.3f}")
    worst = list(res)[-1]
    drop = {c: round(res["clean WAV"]["per_class_f1"][c] - res[worst]["per_class_f1"][c], 2)
            for c in classes}
    print(f"\nclasses losing the most at {worst}:",
          sorted(drop.items(), key=lambda kv: -kv[1])[:4])
    print(f"saved -> {RES / 'telegram_robustness.json'}")


if __name__ == "__main__":
    main()
