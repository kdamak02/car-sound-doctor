"""
Agent 1 - Opus (Telegram) data augmentation for the AST classifier (owner A1/A2).

telegram_robustness.py showed that the clean-trained model loses ~13 macro-F1 points on
Opus 16k audio (domain shift). Here every TRAIN and VAL clip is also passed through Opus at a
random bitrate (12k-32k), embedded with AST, and the classifier is trained on clean + Opus
embeddings. The test set is evaluated both clean and through Opus 16k.

    python agent1_audio/src/ast_augment.py          # ~25 min on CPU, ~3 min on GPU (resumable)

Writes results/ast_augment.json and replaces models/agent1_ast.joblib by the augmented model
(the clean-only one is kept as models/agent1_ast_clean.joblib).
"""
import csv
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ast_train import (DATA, MODELS, RES, Embedder, clip_probs, label_context, predict,  # noqa
                       read_wav, save_cache, windows)
from telegram_robustness import ffmpeg_exe, through_opus  # noqa: E402

BITRATES = ["12k", "16k", "24k", "32k"]
SEED = 42


def load_cache(path):
    if not path.exists():
        return {}
    z = np.load(path, allow_pickle=True)
    return dict(zip(z["files"].tolist(), z["feats"].tolist()))


def embed(rows, path, audio_fn, tag, emb):
    cache = load_cache(path)
    todo = [r for r in rows if r["file"] not in cache]
    t = time.time()
    for i, r in enumerate(todo, 1):
        cache[r["file"]] = emb(windows(audio_fn(r)))
        if i % 50 == 0 or i == len(todo):
            save_cache(path, cache)
            print(f"  [{tag}] {i}/{len(todo)} ({time.time() - t:.0f} s)")
    return cache


def stack(rows, cache):
    X, cid = [], []
    for i, r in enumerate(rows):
        for e in cache[r["file"]]:
            X.append(e)
            cid.append(i)
    return np.array(X), np.array(cid)


def main():
    import joblib
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, f1_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    base = joblib.load(MODELS / "agent1_ast.joblib")
    if "augmented" in base.get("version", ""):
        base = joblib.load(MODELS / "agent1_ast_clean.joblib")
    classes = set(base["classes"])
    ctx_of = label_context()
    rows = [r for r in csv.DictReader(open(DATA / "metadata.csv", encoding="utf-8"))
            if r["split"] in ("train", "val", "test") and r["label"] in classes]
    fit = [r for r in rows if r["split"] in ("train", "val")]
    test = [r for r in rows if r["split"] == "test"]
    rng = np.random.default_rng(SEED)
    br_of = {r["file"]: BITRATES[i] for r, i in zip(fit, rng.integers(0, len(BITRATES), len(fit)))}

    emb, ff = Embedder(), ffmpeg_exe()
    print(f"{len(fit)} train+val clips, {len(test)} test clips, AST on {emb.device}")
    clean = load_cache(RES / "ast_embeddings.npz")
    missing = [r for r in rows if r["file"] not in clean]
    if missing:
        clean = embed(rows, RES / "ast_embeddings.npz", lambda r: read_wav(DATA / r["file"]),
                      "clean", emb)
    with tempfile.TemporaryDirectory() as tmp:
        aug = embed(fit, RES / "ast_embeddings_opus_train.npz",
                    lambda r: through_opus(DATA / r["file"], br_of[r["file"]], ff, tmp),
                    "opus train", emb)
        opus_test = embed(test, RES / "ast_embeddings_opus16k_test.npz",
                          lambda r: through_opus(DATA / r["file"], "16k", ff, tmp),
                          "opus16k test", emb)

    # training set = clean train + Opus train (val kept for model choice by C)
    tr_rows = [r for r in fit if r["split"] == "train"]
    va_rows = [r for r in fit if r["split"] == "val"]
    Xc, cc = stack(tr_rows, clean)
    Xa, ca = stack(tr_rows, aug)
    X_tr = np.vstack([Xc, Xa])
    y_tr = np.concatenate([[tr_rows[i]["label"] for i in cc], [tr_rows[i]["label"] for i in ca]])
    Xv_c, cv_c = stack(va_rows, clean)
    Xv_a, cv_a = stack(va_rows, aug)
    yv = np.array([r["label"] for r in va_rows])

    best, best_f1 = None, -1
    for C in (0.1, 0.3, 1.0):
        m = make_pipeline(StandardScaler(), LogisticRegression(C=C, max_iter=5000,
                                                               class_weight="balanced"))
        m.fit(X_tr, y_tr)
        f = np.mean([f1_score(yv, predict(clip_probs(m, X, c, range(len(va_rows))), m.classes_),
                              average="macro") for X, c in ((Xv_c, cv_c), (Xv_a, cv_a))])
        print(f"  C={C:<4} val macro-F1 (mean clean/opus) {f:.3f}")
        if f > best_f1:
            best, best_f1 = m, f

    y_te = np.array([r["label"] for r in test])
    ctx = np.array([ctx_of[l] for l in y_te])

    def score(model, cache):
        X, c = stack(test, cache)
        P = clip_probs(model, X, c, range(len(test)))
        p, pc = predict(P, model.classes_), predict(P, model.classes_, ctx, ctx_of)
        return {"macro_f1": round(f1_score(y_te, p, average="macro"), 3),
                "accuracy": round(accuracy_score(y_te, p), 3),
                "macro_f1_with_context": round(f1_score(y_te, pc, average="macro"), 3)}

    res = {"trained on clean only": {"clean test": score(base["clf"], clean),
                                     "Opus 16k test": score(base["clf"], opus_test)},
           "trained on clean + Opus": {"clean test": score(best, clean),
                                       "Opus 16k test": score(best, opus_test)},
           "augmentation_bitrates": BITRATES}
    (RES / "ast_augment.json").write_text(json.dumps(res, indent=2), encoding="utf-8")

    if not (MODELS / "agent1_ast_clean.joblib").exists():
        shutil.copy(MODELS / "agent1_ast.joblib", MODELS / "agent1_ast_clean.joblib")
    joblib.dump({**base, "clf": best, "classes": list(best.classes_),
                 "version": "ast-logreg-augmented-opus"}, MODELS / "agent1_ast.joblib")

    print(f"\n{'model':26s} {'test audio':14s} {'macro-F1':>9s} {'accuracy':>9s} {'+context':>9s}")
    for model, d in [(k, v) for k, v in res.items() if isinstance(v, dict)]:
        for audio, s in d.items():
            print(f"{model:26s} {audio:14s} {s['macro_f1']:9.3f} {s['accuracy']:9.3f} "
                  f"{s['macro_f1_with_context']:9.3f}")
    print(f"\naugmented model -> {MODELS / 'agent1_ast.joblib'} "
          f"(clean-only kept as agent1_ast_clean.joblib)")


if __name__ == "__main__":
    main()
