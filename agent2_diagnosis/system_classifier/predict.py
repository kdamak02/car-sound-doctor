"""
Symptom -> car system classifier, step 3: use it. Owner: B2.

    python agent2_diagnosis/system_classifier/predict.py "le moteur cogne quand j'accélère"
    python agent2_diagnosis/system_classifier/predict.py --eval-fr      # score the small French sanity set

In code:
    from agent2_diagnosis.system_classifier.predict import SystemClassifier
    clf = SystemClassifier()
    clf.route("my brakes squeal")          # -> "brakes", or None when the model is not confident
The result is meant for the `system` filter of POST /search: a wrong filter hides the right passages, so route() returns
None below THRESHOLD and the search then runs without a system filter.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL_DIR = HERE / "models" / "xlmr_system"
THRESHOLD = 0.7  # see results.json "by_threshold": coverage vs accuracy trade-off


def clean(text: str) -> str:  # same cleaning as prepare_data.py
    return re.sub(r"\s+", " ", re.sub(r"\*[A-Z]{2,3}\b", " ", text)).strip().lower()


class SystemClassifier:
    def __init__(self, path: Path = MODEL_DIR):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.torch = torch
        self.dev = "cuda" if torch.cuda.is_available() else "cpu"
        self.tok = AutoTokenizer.from_pretrained(path)
        self.model = AutoModelForSequenceClassification.from_pretrained(path).to(self.dev).eval()
        self.max_len = json.loads((path / "max_len.json").read_text())["max_len"]
        self.labels = [self.model.config.id2label[i] for i in range(self.model.config.num_labels)]

    def predict(self, text: str):
        """-> (label, probability, {label: probability})"""
        enc = self.tok(clean(text), truncation=True, max_length=self.max_len, return_tensors="pt").to(self.dev)
        with self.torch.no_grad():
            probs = self.torch.softmax(self.model(**enc).logits.float(), -1)[0].cpu().tolist()
        best = max(range(len(probs)), key=probs.__getitem__)
        return self.labels[best], probs[best], dict(zip(self.labels, probs))

    def route(self, text: str, threshold: float = THRESHOLD):
        label, prob, _ = self.predict(text)
        return label if prob >= threshold else None


if __name__ == "__main__":
    clf = SystemClassifier()
    if sys.argv[1:2] == ["--eval-fr"]:
        rows = [json.loads(line) for line in (HERE / "sanity_fr.jsonl").read_text(encoding="utf-8").splitlines()]
        ok = 0
        for r in rows:
            label, prob, _ = clf.predict(r["text"])
            ok += label == r["label"]
            print(("OK  " if label == r["label"] else "MISS"), f"{prob:.2f} {label:<12} (true {r['label']:<12}) {r['text']}")
        print(f"French sanity set: {ok}/{len(rows)} correct, routed at >= {THRESHOLD}: "
              f"{sum(clf.route(r['text']) == r['label'] for r in rows)} correct, "
              f"{sum(clf.route(r['text']) is None for r in rows)} abstained")
    else:
        label, prob, allp = clf.predict(" ".join(sys.argv[1:]))
        print(label, round(prob, 3), {k: round(v, 3) for k, v in allp.items()})
