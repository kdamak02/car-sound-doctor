"""
Retrieval evaluation. Owner: B2.

    python -m rag.eval.evaluate --url http://localhost:8002 --queries rag/eval/queries.jsonl

queries.jsonl, one JSON per line:
  {"id": "q01", "type": "direct|reworded|ambiguous|no_answer|unknown_car",
   "query": "...", "filters": {...}, "relevant": ["chunk_id", ...]}
For no_answer queries, "relevant" is [] and the system should return no passage
above the score threshold.

Prints Recall@k, MRR and hit rate per query type. Run it for each configuration
(chunking A/B x dense/hybrid x rerank on/off) and paste the table in the report.
"""
import argparse
import json
from collections import defaultdict
from urllib import request


def call(url, q, k, rerank):
    body = json.dumps({"query": q["query"], "filters": q.get("filters", {}),
                       "top_k": k, "rerank": rerank}).encode()
    req = request.Request(url.rstrip("/") + "/search", data=body,
                          headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def score(results, relevant, k):
    ids = [p["chunk_id"] for p in results["passages"][:k]]
    rel = set(relevant)
    recall = len(rel & set(ids)) / len(rel) if rel else None
    rr = next((1 / (i + 1) for i, c in enumerate(ids) if c in rel), 0.0)
    return recall, rr, float(any(c in rel for c in ids))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8002")
    ap.add_argument("--queries", default="rag/eval/queries.jsonl")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--no-rerank", action="store_true")
    ap.add_argument("--threshold", type=float, default=0.5,
                    help="score under which a passage counts as 'no answer'")
    a = ap.parse_args()

    by_type = defaultdict(lambda: {"recall": [], "rr": [], "hit": [], "rejected": []})
    for line in open(a.queries, encoding="utf-8"):
        if not line.strip():
            continue
        q = json.loads(line)
        res = call(a.url, q, a.k, not a.no_rerank)
        t = by_type[q.get("type", "direct")]
        if not q["relevant"]:
            t["rejected"].append(all(p["score"] < a.threshold for p in res["passages"]))
            continue
        r, rr, hit = score(res, q["relevant"], a.k)
        t["recall"].append(r), t["rr"].append(rr), t["hit"].append(hit)

    mean = lambda v: f"{sum(v) / len(v):.2f}" if v else "  - "
    print(f"{'type':12s} {'n':>3s} {'R@' + str(a.k):>6s} {'MRR':>6s} {'hit':>6s} {'no-ans ok':>9s}")
    for typ, t in sorted(by_type.items()):
        n = len(t["recall"]) + len(t["rejected"])
        print(f"{typ:12s} {n:3d} {mean(t['recall']):>6s} {mean(t['rr']):>6s} "
              f"{mean(t['hit']):>6s} {mean(t['rejected']):>9s}")


if __name__ == "__main__":
    main()
