"""contracts/labels.json for Agent 2, in both formats (owner B2).

Version 0.1: `labels` = trained classes and a separate `hypotheses_only.ids`.
Version 0.2 (group A): `labels` holds EVERY label; a label is trained only if Agent 1 had enough recordings, and the
trained ones are the classes of Agent 1's model (agent1_audio/results/ast.json, key `labels`). The faults that only
Agent 2 may propose are the others. This function returns the 0.1 shape in both cases: labels["hypotheses_only"]["ids"].
"""
import json
from pathlib import Path


def load_labels(root: Path) -> dict:
    labels = json.loads((root / "contracts/labels.json").read_text("utf-8"))
    if "hypotheses_only" not in labels:
        res = root / "agent1_audio/results/ast.json"
        trained = set(json.loads(res.read_text("utf-8")).get("labels", [])) if res.exists() else set()
        labels["hypotheses_only"] = {"ids": [x["id"] for x in labels["labels"] if trained and x["id"] not in trained]}
    return labels
