"""
Symptom -> car system classifier, step 1: NHTSA complaints -> train/val/test files. Owner: B2.

    python agent2_diagnosis/system_classifier/prepare_data.py [path/to/complaints_engine_brakes.jsonl]

Input : the file written by data/download_rag.py (rag/raw/nhtsa/complaints_engine_brakes.jsonl), one complaint per line.
Label : NHTSA `components` mapped to our `system` values (contracts/rag_search.schema.json). A complaint is kept only when
        exactly ONE of our systems appears in its components (multi-system complaints are ambiguous, dropped).
Output: agent2_diagnosis/system_classifier/data/{train,val,test}.jsonl  (stratified 80/10/10, seed 0) + data/stats.json
"""
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_IN = ROOT / "data/rag/raw/nhtsa/complaints_engine_brakes.jsonl"
OUT = HERE / "data"

# substring of the NHTSA components string -> our `system` value
SYSTEMS = [("POWER TRAIN", "transmission"), ("ENGINE", "engine"), ("ELECTRICAL SYSTEM", "electrical"),
           ("FUEL", "fuel"), ("BRAKES", "brakes"), ("EXHAUST", "exhaust"), ("STEERING", "steering"),
           ("SUSPENSION", "suspension")]
MIN_WORDS = 5
MIN_PER_CLASS = 100  # a class with fewer examples is dropped (cannot be learned or evaluated)


def clean(text: str) -> str:
    text = re.sub(r"\*[A-Z]{2,3}\b", " ", text)  # NHTSA redaction marks such as *TR, *JB
    return re.sub(r"\s+", " ", text).strip().lower()


def label_of(components: str):
    found = {system for key, system in SYSTEMS if key in components.upper()}
    return found.pop() if len(found) == 1 else None


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_IN
    rows, seen = [], set()
    for line in src.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        text, label = clean(r["summary"]), label_of(r["components"])
        if label and len(text.split()) >= MIN_WORDS and text not in seen:
            seen.add(text)
            rows.append({"text": text, "label": label, "odi": r["odi_number"], "make": r["make"], "model": r["model"]})
    counts = Counter(r["label"] for r in rows)
    keep = {k for k, v in counts.items() if v >= MIN_PER_CLASS}
    rows = [r for r in rows if r["label"] in keep]

    by_label = defaultdict(list)
    for r in rows:
        by_label[r["label"]].append(r)
    rng = random.Random(0)
    split = {"train": [], "val": [], "test": []}
    for items in by_label.values():
        rng.shuffle(items)
        n = len(items)
        a, b = int(0.8 * n), int(0.9 * n)
        split["train"] += items[:a]
        split["val"] += items[a:b]
        split["test"] += items[b:]
    OUT.mkdir(exist_ok=True)
    for name, items in split.items():
        rng.shuffle(items)
        (OUT / f"{name}.jsonl").write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n", encoding="utf-8")
    stats = {"kept": len(rows), "dropped_classes": sorted(set(counts) - keep), "per_class": dict(Counter(r["label"] for r in rows)),
             "split": {k: len(v) for k, v in split.items()}}
    (OUT / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(json.dumps(stats, indent=1))


if __name__ == "__main__":
    main()
