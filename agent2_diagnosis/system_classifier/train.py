"""
Symptom -> car system classifier, step 2: baseline + fine-tuning. Owner: B2.

    python agent2_diagnosis/system_classifier/train.py                 # baseline, then fine-tune xlm-roberta-base
    python agent2_diagnosis/system_classifier/train.py --baseline-only
    python agent2_diagnosis/system_classifier/train.py --epochs 3 --model xlm-roberta-base

Needs data/ from prepare_data.py. Writes models/ (git-ignored) and results.json (committed: numbers for the report).
Both models are scored on the same held-out test set: accuracy, macro-F1, per-class F1, confusion matrix, and
coverage/accuracy at several confidence thresholds (Agent 2 only uses the prediction when it is confident).
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score

HERE = Path(__file__).resolve().parent
DATA, MODELS = HERE / "data", HERE / "models"
THRESHOLDS = [0.5, 0.6, 0.7, 0.8, 0.9]


def load(name):
    return [json.loads(line) for line in (DATA / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]


def report(y_true, probs, labels):
    pred = probs.argmax(1)
    conf = probs.max(1)
    rep = classification_report(y_true, pred, target_names=labels, output_dict=True, zero_division=0)
    return {
        "accuracy": round(accuracy_score(y_true, pred), 4),
        "macro_f1": round(f1_score(y_true, pred, average="macro"), 4),
        "per_class_f1": {l: round(rep[l]["f1-score"], 3) for l in labels},
        "confusion_matrix": {"labels": labels, "rows_true_cols_pred": confusion_matrix(y_true, pred).tolist()},
        "by_threshold": {str(t): {"coverage": round(float((conf >= t).mean()), 3),
                                  "accuracy_when_used": round(float((pred[conf >= t] == y_true[conf >= t]).mean()), 3)
                                  if (conf >= t).any() else None} for t in THRESHOLDS},
    }


def baseline(train, test, labels):
    idx = {l: i for i, l in enumerate(labels)}
    vec = TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True, max_features=200_000)
    x_train = vec.fit_transform([r["text"] for r in train])
    clf = LogisticRegression(max_iter=2000, C=5, class_weight="balanced").fit(x_train, [idx[r["label"]] for r in train])
    probs = clf.predict_proba(vec.transform([r["text"] for r in test]))
    return report(np.array([idx[r["label"]] for r in test]), probs, labels)


def finetune(train, val, test, labels, model_name, epochs, batch, lr, max_len):
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup

    torch.manual_seed(0)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    idx = {l: i for i, l in enumerate(labels)}
    tok = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_name, num_labels=len(labels), id2label=dict(enumerate(labels)), label2id=idx).to(dev)

    def batches(rows, shuffle):
        order = np.random.RandomState(0).permutation(len(rows)) if shuffle else np.arange(len(rows))
        for i in range(0, len(rows), batch):
            part = [rows[j] for j in order[i:i + batch]]
            enc = tok([r["text"] for r in part], truncation=True, max_length=max_len, padding=True, return_tensors="pt")
            yield {k: v.to(dev) for k, v in enc.items()}, torch.tensor([idx[r["label"]] for r in part]).to(dev)

    @torch.no_grad()
    def predict(rows):
        model.eval()
        out = []
        for enc, _ in batches(rows, False):
            with torch.autocast(dev, enabled=dev == "cuda"):
                out.append(torch.softmax(model(**enc).logits.float(), -1).cpu().numpy())
        return np.concatenate(out)

    counts = np.bincount([idx[r["label"]] for r in train], minlength=len(labels))
    weights = torch.tensor(len(train) / (len(labels) * counts), dtype=torch.float).to(dev)  # class imbalance
    loss_fn = torch.nn.CrossEntropyLoss(weight=weights)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
    steps = epochs * ((len(train) + batch - 1) // batch)
    sched = get_linear_schedule_with_warmup(opt, int(0.06 * steps), steps)
    scaler = torch.amp.GradScaler(enabled=dev == "cuda")
    y_val = np.array([idx[r["label"]] for r in val])
    best, t0, step = -1.0, time.time(), 0

    for epoch in range(epochs):
        model.train()
        order_seed = epoch  # different shuffle each epoch
        rows = [train[j] for j in np.random.RandomState(order_seed).permutation(len(train))]
        for enc, y in batches(rows, False):
            with torch.autocast(dev, enabled=dev == "cuda"):
                loss = loss_fn(model(**enc).logits.float(), y)
            opt.zero_grad()
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(opt)
            scaler.update()
            sched.step()
            step += 1
            if step % 100 == 0:
                print(f"epoch {epoch + 1} step {step}/{steps} loss {loss.item():.3f} ({time.time() - t0:.0f}s)", flush=True)
        f1 = f1_score(y_val, predict(val).argmax(1), average="macro")
        print(f"epoch {epoch + 1}: val macro-F1 {f1:.4f}", flush=True)
        if f1 > best:
            best = f1
            MODELS.mkdir(exist_ok=True)
            model.save_pretrained(MODELS / "xlmr_system")
            tok.save_pretrained(MODELS / "xlmr_system")
            (MODELS / "xlmr_system" / "max_len.json").write_text(json.dumps({"max_len": max_len}))

    model = AutoModelForSequenceClassification.from_pretrained(MODELS / "xlmr_system").to(dev)  # best epoch
    res = report(np.array([idx[r["label"]] for r in test]), predict(test), labels)
    res.update(best_val_macro_f1=round(best, 4), train_seconds=round(time.time() - t0), model=model_name,
               epochs=epochs, batch=batch, lr=lr, max_len=max_len, device=dev)
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="xlm-roberta-base")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--max-len", type=int, default=192)
    ap.add_argument("--baseline-only", action="store_true")
    a = ap.parse_args()

    train, val, test = load("train"), load("val"), load("test")
    labels = sorted({r["label"] for r in train})
    results = {"labels": labels, "n_train": len(train), "n_val": len(val), "n_test": len(test),
               "tfidf_logreg": baseline(train, test, labels)}
    print("baseline TF-IDF + LogReg:", {k: results["tfidf_logreg"][k] for k in ("accuracy", "macro_f1")}, flush=True)
    path = HERE / "results.json"
    path.write_text(json.dumps(results, indent=1), encoding="utf-8")
    if not a.baseline_only:
        results["finetuned"] = finetune(train, val, test, labels, a.model, a.epochs, a.batch, a.lr, a.max_len)
        print("fine-tuned:", {k: results["finetuned"][k] for k in ("accuracy", "macro_f1")})
        path.write_text(json.dumps(results, indent=1), encoding="utf-8")
