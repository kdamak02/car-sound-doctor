"""
Validate JSON against the contracts.

    pip install jsonschema
    python contracts/validate.py                       # check every example (also run by CI)
    python contracts/validate.py agent2_output my.json # check one of your own outputs

Importable: from contracts.validate import validate  ->  validate("agent3_output", obj)
"""
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

HERE = Path(__file__).resolve().parent

EXAMPLES = {
    "agent1_output": "agent1_output.json",
    "agent2_output": "agent2_output.json",
    "agent3_output": "agent3_output.json",
    "case_record": "case_record.json",
    "rag_search#request": "rag_search_request.json",
    "rag_search#response": "rag_search_response.json",
}


def _registry():
    reg = Registry()
    for p in HERE.glob("*.schema.json"):
        reg = reg.with_resource(p.name, Resource.from_contents(json.loads(p.read_text("utf-8"))))
    return reg


def _schema(name):
    base, _, sub = name.partition("#")
    schema = json.loads((HERE / f"{base}.schema.json").read_text("utf-8"))
    if sub:
        return {"$ref": f"{base}.schema.json#/$defs/{sub}"}
    return schema


def errors(name, obj):
    v = Draft202012Validator(_schema(name), registry=_registry())
    return [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
            for e in v.iter_errors(obj)]


def validate(name, obj):
    errs = errors(name, obj)
    if errs:
        raise ValueError(f"{name} invalid:\n  " + "\n  ".join(errs))
    return True


def check_labels(obj, labels):
    """Labels used by Agent 1 must exist in labels.json."""
    bad = [t["label"] for t in obj.get("top3", []) if t["label"] not in labels]
    if obj.get("label") and obj["label"] not in labels:
        bad.append(obj["label"])
    return bad


def main():
    labels = {l["id"] for l in json.loads((HERE / "labels.json").read_text("utf-8"))["labels"]}
    if len(sys.argv) == 3:
        obj = json.loads(Path(sys.argv[2]).read_text("utf-8"))
        errs = errors(sys.argv[1], obj)
        print("\n".join(errs) if errs else "valid")
        sys.exit(1 if errs else 0)
    failed = 0
    for name, file in EXAMPLES.items():
        obj = json.loads((HERE / "examples" / file).read_text("utf-8"))
        errs = errors(name, obj)
        if name == "agent1_output":
            errs += [f"unknown label {b}" for b in check_labels(obj, labels)]
        print(f"{'OK  ' if not errs else 'FAIL'} {file}")
        for e in errs:
            print("     ", e)
        failed += bool(errs)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
