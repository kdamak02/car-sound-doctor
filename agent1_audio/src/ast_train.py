"""
Agent 1 - AST embeddings + light classifier (owner A2). Run on a GPU (Lightning AI, Kaggle,
Colab); also works on CPU, just slower (~15-25 min for the embeddings).

    pip install -r agent1_audio/requirements-train.txt
    python agent1_audio/src/ast_train.py --limit 30     # smoke test, 1 minute
    python agent1_audio/src/ast_train.py                # full run

Steps
 1. Each clip is cut into the same 1.5 s windows as the baseline.
 2. AST (MIT/ast-finetuned-audioset-10-10-0.4593, pre-trained on AudioSet) turns each window
    into a 768-number embedding. Cached in results/ast_embeddings.npz (resumable).
 3. Logistic regression / SVM on the embeddings, chosen on the validation split.
    Evaluated with and without the context hint (idle / startup / braking).
 4. Anomaly score: Isolation Forest on healthy embeddings only.
 5. Saves models/agent1_ast.joblib, used by src/model.py (service in real mode).

Outputs: agent1_audio/results/ast.json, ast_confusion.png, ast_vs_baseline.txt
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
DATA = ROOT / "data" / "audio" / "processed"
RES = ROOT / "agent1_audio" / "results"
MODELS = ROOT / "models"
SR, WIN = 16000, 1.5
AST_ID = "MIT/ast-finetuned-audioset-10-10-0.4593"
HEALTHY = {"normal", "normal_brakes", "normal_startup"}
SEED = 42


# ------------------------------------------------------------------ embeddings
class Embedder:
    """AST encoder -> 768-d pooled embedding per 1.5 s window."""

    def __init__(self, device=None):
        import torch
        from transformers import ASTFeatureExtractor, ASTModel
        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.fe = ASTFeatureExtractor.from_pretrained(AST_ID)
        self.model = ASTModel.from_pretrained(AST_ID).to(self.device).eval()

    def __call__(self, wins, batch=32):
        out = []
        for i in range(0, len(wins), batch):
            inp = self.fe([w for w in wins[i:i + batch]], sampling_rate=SR, return_tensors="pt")
            with self.torch.no_grad():
                h = self.model(inp["input_values"].to(self.device)).pooler_output
            out.append(h.float().cpu().numpy())
        return np.concatenate(out)


def windows(x):
    n = int(SR * WIN)
    if len(x) < n:
        return [np.pad(x, (0, n - len(x)))]
    return [x[i:i + n] for i in range(0, len(x) - n + 1, n)]


def read_wav(p):
    import soundfile as sf
    x, sr = sf.read(str(p), dtype="float32")
    assert sr == SR, f"{p} is {sr} Hz, run prepare_data.py first"
    return x if x.ndim == 1 else x.mean(1)


def save_cache(path, cache):
    feats = np.empty(len(cache), dtype=object)
    for i, v in enumerate(cache.values()):
        feats[i] = v
    np.savez(path, files=np.array(list(cache), dtype=object), feats=feats)


def embed_all(rows, limit=None):
    path = RES / "ast_embeddings.npz"
    cache = {}
    if path.exists():
        z = np.load(path, allow_pickle=True)
        cache = dict(zip(z["files"].tolist(), z["feats"].tolist()))
    todo = [r for r in rows if r["file"] not in cache][:limit]
    if todo:
        emb = Embedder()
        print(f"AST on {emb.device}: {len(todo)} clips to embed")
        t = time.time()
        for i, r in enumerate(todo, 1):
            cache[r["file"]] = emb(windows(read_wav(DATA / r["file"])))
            if i % 100 == 0 or i == len(todo):
                save_cache(path, cache)
                print(f"  {i}/{len(todo)}  ({time.time() - t:.0f} s)")
    return cache


# ------------------------------------------------------------------ evaluation
def label_context():
    d = json.loads((ROOT / "contracts" / "labels.json").read_text("utf-8"))
    return {l["id"]: l["context"] for l in d["labels"]}


def clip_probs(model, X, clip_id, clips):
    P = model.predict_proba(X)
    return np.array([P[clip_id == c].mean(0) for c in clips])


def predict(P, classes, contexts=None, ctx_of=None):
    """argmax, optionally restricted to the labels of the clip's context."""
    if contexts is None:
        return classes[P.argmax(1)]
    out = []
    for p, ctx in zip(P, contexts):
        mask = np.array([ctx_of[c] == ctx for c in classes])
        out.append(classes[np.where(mask, p, -1).argmax()] if mask.any() else classes[p.argmax()])
    return np.array(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="embed only N new clips (smoke test)")
    a = ap.parse_args()

    from sklearn.ensemble import IsolationForest
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import (accuracy_score, classification_report, confusion_matrix,
                                 f1_score, roc_auc_score)
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.svm import SVC
    import joblib

    RES.mkdir(parents=True, exist_ok=True)
    MODELS.mkdir(parents=True, exist_ok=True)
    rows = [r for r in csv.DictReader(open(DATA / "metadata.csv", encoding="utf-8"))
            if r["split"] != "hypothesis"]
    cache = embed_all(rows, a.limit)
    rows = [r for r in rows if r["file"] in cache]
    if a.limit:
        print(f"smoke test OK: {len(rows)} clips embedded, dim {next(iter(cache.values())).shape}")
        return

    X, clip_id = [], []
    for i, r in enumerate(rows):
        for e in cache[r["file"]]:
            X.append(e)
            clip_id.append(i)
    X, clip_id = np.array(X), np.array(clip_id)
    y = np.array([r["label"] for r in rows])
    split = np.array([r["split"] for r in rows])
    ctx_of = label_context()
    ctx_true = np.array([ctx_of[l] for l in y])
    tr = np.isin(clip_id, np.where(split == "train")[0])
    val, test = np.where(split == "val")[0], np.where(split == "test")[0]
    print(f"{len(rows)} clips, {len(X)} windows, embedding dim {X.shape[1]}")

    candidates = {
        "logreg_C0.1": make_pipeline(StandardScaler(), LogisticRegression(
            C=0.1, max_iter=5000, class_weight="balanced")),
        "logreg_C1": make_pipeline(StandardScaler(), LogisticRegression(
            C=1.0, max_iter=5000, class_weight="balanced")),
        "svm_rbf": make_pipeline(StandardScaler(), SVC(
            C=10, probability=True, class_weight="balanced", random_state=SEED)),
    }
    scores = {}
    for name, m in candidates.items():
        m.fit(X[tr], y[clip_id[tr]])
        pv = predict(clip_probs(m, X, clip_id, val), m.classes_)
        scores[name] = f1_score(y[val], pv, average="macro")
        print(f"  {name:12s} val macro-F1 {scores[name]:.3f}")
    best = max(scores, key=scores.get)
    clf = candidates[best]
    classes = clf.classes_
    Pt = clip_probs(clf, X, clip_id, test)
    p_free = predict(Pt, classes)
    p_ctx = predict(Pt, classes, ctx_true[test], ctx_of)

    # anomaly detector on healthy training windows
    healthy_tr = tr & np.isin(y[clip_id], list(HEALTHY))
    sc = StandardScaler().fit(X[healthy_tr])
    iso = IsolationForest(n_estimators=400, random_state=SEED).fit(sc.transform(X[healthy_tr]))
    s = -iso.score_samples(sc.transform(X))
    clip_s = np.array([s[clip_id == c].mean() for c in test])
    auc = roc_auc_score(~np.isin(y[test], list(HEALTHY)), clip_s)
    ref = np.sort(-iso.score_samples(sc.transform(X[healthy_tr])))   # to map score -> 0..1

    res = {
        "embedding": AST_ID, "window_s": WIN, "best_model": best,
        "val_macro_f1": {k: round(v, 3) for k, v in scores.items()},
        "test_macro_f1": round(f1_score(y[test], p_free, average="macro"), 3),
        "test_accuracy": round(accuracy_score(y[test], p_free), 3),
        "test_macro_f1_with_context": round(f1_score(y[test], p_ctx, average="macro"), 3),
        "test_accuracy_with_context": round(accuracy_score(y[test], p_ctx), 3),
        "anomaly_auc": round(auc, 3),
        "per_class": classification_report(y[test], p_free, output_dict=True, zero_division=0),
        "labels": list(classes),
        "confusion": confusion_matrix(y[test], p_free, labels=classes).tolist(),
    }
    (RES / "ast.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    joblib.dump({"clf": clf, "classes": list(classes), "iso": iso, "iso_scaler": sc,
                 "iso_ref": ref, "ctx_of": ctx_of, "ast_id": AST_ID, "window_s": WIN,
                 "version": f"ast-{best}"}, MODELS / "agent1_ast.joblib")

    from baseline_mfcc import plot
    plot(res["labels"], res["confusion"], RES / "ast_confusion.png")

    lines = ["model                          macro-F1   accuracy   anomaly AUC"]
    base = RES / "baseline_mfcc.json"
    if base.exists():
        b = json.loads(base.read_text("utf-8"))
        g = b["group_split"]
        lines.append(f"MFCC + {g['best_model']:22s} {g['test_macro_f1']:8.3f}   "
                     f"{g['test_accuracy']:8.3f}   {b['anomaly_isolation_forest']['test_auc_fault_vs_healthy']:8.3f}")
    lines.append(f"AST  + {best:22s} {res['test_macro_f1']:8.3f}   {res['test_accuracy']:8.3f}"
                 f"   {res['anomaly_auc']:8.3f}")
    lines.append(f"AST  + {best + ' + context':22s} {res['test_macro_f1_with_context']:8.3f}   "
                 f"{res['test_accuracy_with_context']:8.3f}")
    table = "\n".join(lines)
    (RES / "ast_vs_baseline.txt").write_text(table + "\n", encoding="utf-8")
    print("\n" + table)
    print("\nper class F1 (AST, no context):")
    for lab in classes:
        print(f"  {lab:16s} {res['per_class'][lab]['f1-score']:.2f}")
    print(f"\nmodel -> {MODELS / 'agent1_ast.joblib'}   results -> {RES}")


if __name__ == "__main__":
    main()
