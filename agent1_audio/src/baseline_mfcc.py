"""
Agent 1 baseline (owner A2): MFCC statistics + classic classifiers, and an
Isolation Forest anomaly detector trained on healthy sounds only.

    pip install librosa scikit-learn matplotlib
    python agent1_audio/src/baseline_mfcc.py        # from the repo root (CPU, a few minutes)

Every clip is cut into 1.5 s windows (the length of the Kaggle clips), so that the model
cannot use clip length to recognise the dataset. A clip's prediction is the mean of its
windows' probabilities.

Outputs in agent1_audio/results/:
  baseline_mfcc.json            all metrics
  baseline_mfcc_confusion.png   test confusion matrix
"""
import csv
import json
import time
from collections import defaultdict
from pathlib import Path

import librosa
import numpy as np
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, classification_report, confusion_matrix, f1_score,
                             roc_auc_score)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "audio" / "processed"
RES = ROOT / "agent1_audio" / "results"
SR, WIN = 16000, 1.5
HEALTHY = {"normal", "normal_brakes", "normal_startup"}
SEED = 42


def windows(x):
    n = int(SR * WIN)
    if len(x) < n:
        return [np.pad(x, (0, n - len(x)))]
    return [x[i:i + n] for i in range(0, len(x) - n + 1, n)]


def features(w):
    """MFCC + deltas + a few spectral descriptors -> mean and std over time (100 values)."""
    m = librosa.feature.mfcc(y=w, sr=SR, n_mfcc=20, n_fft=1024, hop_length=256)
    d = librosa.feature.delta(m)
    extra = np.vstack([
        librosa.feature.spectral_centroid(y=w, sr=SR, n_fft=1024, hop_length=256),
        librosa.feature.spectral_bandwidth(y=w, sr=SR, n_fft=1024, hop_length=256),
        librosa.feature.spectral_rolloff(y=w, sr=SR, n_fft=1024, hop_length=256),
        librosa.feature.zero_crossing_rate(w, frame_length=1024, hop_length=256),
        librosa.feature.rms(y=w, frame_length=1024, hop_length=256),
    ])
    f = np.vstack([m, d, np.log1p(extra)])
    return np.concatenate([f.mean(1), f.std(1)])


def load():
    """Features per clip, cached in results/features_cache.npz (resumable)."""
    rows = [r for r in csv.DictReader(open(DATA / "metadata.csv", encoding="utf-8"))
            if r["split"] != "hypothesis"]
    cache_path = RES / "features_cache.npz"
    cache = {}
    if cache_path.exists():
        z = np.load(cache_path, allow_pickle=True)
        cache = dict(zip(z["files"].tolist(), z["feats"].tolist()))
    t, new = time.time(), 0
    for i, r in enumerate(rows):
        if r["file"] in cache:
            continue
        x, _ = librosa.load(DATA / r["file"], sr=SR, mono=True)
        cache[r["file"]] = np.array([features(w) for w in windows(x)])
        new += 1
        if new % 200 == 0:
            np.savez(cache_path, files=np.array(list(cache), dtype=object),
                     feats=np.array(list(cache.values()), dtype=object))
            print(f"  features {len(cache)}/{len(rows)}  ({time.time() - t:.0f} s)")
    if new:
        np.savez(cache_path, files=np.array(list(cache), dtype=object),
                 feats=np.array(list(cache.values()), dtype=object))
    X, clip_id = [], []
    for i, r in enumerate(rows):
        for f in cache[r["file"]]:
            X.append(f)
            clip_id.append(i)
    return rows, np.array(X, dtype=np.float64), np.array(clip_id)


def clip_level(model, X, clip_id, idx_clips, classes):
    """Average window probabilities per clip."""
    P = model.predict_proba(X)
    out = []
    for c in idx_clips:
        out.append(P[clip_id == c].mean(0))
    return classes[np.argmax(np.array(out), 1)]


def run(rows, X, clip_id, split_of_clip, tag):
    y_clip = np.array([r["label"] for r in rows])
    tr = np.isin(clip_id, [i for i, s in enumerate(split_of_clip) if s == "train"])
    val_clips = [i for i, s in enumerate(split_of_clip) if s == "val"]
    test_clips = [i for i, s in enumerate(split_of_clip) if s == "test"]
    y_win = y_clip[clip_id]
    models = {
        "logreg": make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000, C=1.0,
                                                                     class_weight="balanced")),
        "svm_rbf": make_pipeline(StandardScaler(), SVC(C=10, probability=True,
                                                       class_weight="balanced",
                                                       random_state=SEED)),
        "random_forest": RandomForestClassifier(n_estimators=400, class_weight="balanced",
                                                random_state=SEED, n_jobs=-1),
    }
    res = {}
    for name, m in models.items():
        m.fit(X[tr], y_win[tr])
        classes = m.classes_
        pv = clip_level(m, X, clip_id, val_clips, classes)
        res[name] = {"val_macro_f1": f1_score(y_clip[val_clips], pv, average="macro"),
                     "model": m}
        print(f"  [{tag}] {name:14s} val macro-F1 {res[name]['val_macro_f1']:.3f}")
    best = max(res, key=lambda k: res[k]["val_macro_f1"])
    m = res[best]["model"]
    pt = clip_level(m, X, clip_id, test_clips, m.classes_)
    yt = y_clip[test_clips]
    return {"best_model": best,
            "val_macro_f1": {k: round(v["val_macro_f1"], 3) for k, v in res.items()},
            "test_accuracy": round(accuracy_score(yt, pt), 3),
            "test_macro_f1": round(f1_score(yt, pt, average="macro"), 3),
            "per_class": classification_report(yt, pt, output_dict=True, zero_division=0),
            "labels": list(m.classes_),
            "confusion": confusion_matrix(yt, pt, labels=m.classes_).tolist(),
            "_test": (test_clips, yt, pt)}


def anomaly(rows, X, clip_id, split_of_clip):
    """Isolation Forest trained on healthy windows of the train split only."""
    y = np.array([r["label"] for r in rows])
    healthy = np.isin(y, list(HEALTHY))
    tr = np.isin(clip_id, [i for i, s in enumerate(split_of_clip) if s == "train" and healthy[i]])
    test_clips = [i for i, s in enumerate(split_of_clip) if s == "test"]
    sc = StandardScaler().fit(X[tr])
    iso = IsolationForest(n_estimators=400, random_state=SEED).fit(sc.transform(X[tr]))
    s = -iso.score_samples(sc.transform(X))          # higher = more anomalous
    clip_score = np.array([s[clip_id == c].mean() for c in test_clips])
    is_fault = ~healthy[test_clips]
    by_label = defaultdict(list)
    for c, sc_ in zip(test_clips, clip_score):
        by_label[y[c]].append(float(sc_))
    return {"test_auc_fault_vs_healthy": round(roc_auc_score(is_fault, clip_score), 3),
            "mean_score_by_label": {k: round(float(np.mean(v)), 3) for k, v in sorted(by_label.items())}}


def per_dataset(rows, test_clips, yt, pt):
    out = defaultdict(lambda: [0, 0])
    for c, a, b in zip(test_clips, yt, pt):
        out[rows[c]["dataset"]][0] += int(a == b)
        out[rows[c]["dataset"]][1] += 1
    return {k: f"{v[0]}/{v[1]} ({v[0] / v[1]:.0%})" for k, v in out.items()}


def plot(labels, cm, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    cm = np.array(cm)
    norm = cm / cm.sum(1, keepdims=True).clip(min=1)
    fig, ax = plt.subplots(figsize=(8, 7))
    ax.imshow(norm, cmap="Blues", vmin=0, vmax=1)
    ax.set_xticks(range(len(labels)), labels, rotation=45, ha="right")
    ax.set_yticks(range(len(labels)), labels)
    for i in range(len(labels)):
        for j in range(len(labels)):
            if cm[i, j]:
                ax.text(j, i, cm[i, j], ha="center", va="center", fontsize=8,
                        color="white" if norm[i, j] > 0.5 else "black")
    ax.set_xlabel("predicted")
    ax.set_ylabel("true")
    ax.set_title("Baseline MFCC - test set (clips)")
    fig.tight_layout()
    fig.savefig(path, dpi=130)


def main():
    RES.mkdir(parents=True, exist_ok=True)
    rows, X, clip_id = load()
    print(f"{len(rows)} clips -> {len(X)} windows of {WIN} s, {X.shape[1]} features")

    group_split = [r["split"] for r in rows]
    print("\nRecording-level split (the honest one):")
    honest = run(rows, X, clip_id, group_split, "group split")

    # same models with a naive random clip split, to measure leakage
    idx = np.arange(len(rows))
    y = [r["label"] for r in rows]
    tr, rest = train_test_split(idx, test_size=0.30, stratify=y, random_state=SEED)
    va, te = train_test_split(rest, test_size=0.5, stratify=[y[i] for i in rest],
                              random_state=SEED)
    rnd = np.array(["train"] * len(rows), dtype=object)
    rnd[va], rnd[te] = "val", "test"
    print("\nNaive random split (for comparison only):")
    naive = run(rows, X, clip_id, list(rnd), "random split")

    print("\nAnomaly detection (Isolation Forest, healthy sounds only):")
    ano = anomaly(rows, X, clip_id, group_split)
    print(f"  test AUC fault vs healthy: {ano['test_auc_fault_vs_healthy']}")

    test_clips, yt, pt = honest.pop("_test")
    naive.pop("_test")
    honest["test_accuracy_by_dataset"] = per_dataset(rows, test_clips, yt, pt)
    plot(honest["labels"], honest["confusion"], RES / "baseline_mfcc_confusion.png")
    out = {"features": "MFCC(20)+delta+5 spectral, mean/std, 1.5 s windows",
           "clips": len(rows), "windows": int(len(X)),
           "group_split": honest, "random_split": naive, "anomaly_isolation_forest": ano}
    (RES / "baseline_mfcc.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    print("\n================ SUMMARY ================")
    print(f"best model: {honest['best_model']}")
    print(f"test macro-F1  group split : {honest['test_macro_f1']}   "
          f"(accuracy {honest['test_accuracy']})")
    print(f"test macro-F1  random split: {naive['test_macro_f1']}   <- optimistic (leakage)")
    print(f"accuracy by dataset: {honest['test_accuracy_by_dataset']}")
    print("per class F1 (group split):")
    for lab in honest["labels"]:
        pc = honest["per_class"][lab]
        print(f"  {lab:16s} F1 {pc['f1-score']:.2f}  (n={int(pc['support'])})")
    print(f"anomaly AUC: {ano['test_auc_fault_vs_healthy']}")
    print(f"\nsaved -> {RES}")


if __name__ == "__main__":
    main()
