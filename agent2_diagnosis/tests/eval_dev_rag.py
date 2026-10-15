"""
Retrieval evaluation of a running /search service (owner B2): Recall@5, MRR, hit rate, no-answer rejection.

    python agent2_diagnosis/tests/eval_dev_rag.py [--url http://localhost:8002] [--sections symptoms,causes,codes] [-v]

Queries: rag/eval/queries.jsonl (made by make_eval_queries.py). A returned passage is correct when its (manual, page)
is one of `relevant_pages`; for the fixed-window index a window starting one page earlier also counts (500 words
overlap the next page). Answerable = direct, reworded, ambiguous, unknown_car. no_answer is correct when the service
returns no passage at all (it applies its own score threshold).
Run it once per configuration (service restarted with DEV_RAG_STRATEGY / DEV_RAG_HYBRID) and with / without --sections.
"""
import argparse
import json
import statistics
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
K = 5


def post(url, body):
    req = urllib.request.Request(url.rstrip("/") + "/search", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json; charset=utf-8"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def matches(p, rel, fixed):
    return any(p["source"] == r["source"] and (p["page"] == r["page"] or (fixed and p["page"] == r["page"] - 1)) for r in rel)


def score(passages, rel, fixed):
    """-> (recall over relevant (source,page) pairs, reciprocal rank of the first correct passage, hit)"""
    covered = {(r["source"], r["page"]) for r in rel if any(matches(p, [r], fixed) for p in passages)}
    rank = next((i + 1 for i, p in enumerate(passages) if matches(p, rel, fixed)), None)
    pairs = {(r["source"], r["page"]) for r in rel}
    return len(covered) / len(pairs), (1 / rank if rank else 0.0), rank is not None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8002")
    ap.add_argument("--queries", default=str(ROOT / "rag/eval/queries.jsonl"))
    ap.add_argument("--sections", default="", help="comma list, e.g. symptoms,causes,codes (what Agent 2 asks for); empty = all sections")
    ap.add_argument("-v", action="store_true", help="list the queries that missed")
    ap.add_argument("--json", action="store_true", help="print one JSON line for the summary")
    a = ap.parse_args()
    health = json.loads(urllib.request.urlopen(a.url.rstrip("/") + "/health", timeout=10).read())
    fixed = health.get("strategy") == "fixed"
    sections = [s for s in a.sections.split(",") if s]

    per_type = defaultdict(lambda: {"recall": [], "rr": [], "hit": [], "rejected": [], "top": []})
    misses = []
    for line in Path(a.queries).read_text(encoding="utf-8").splitlines():
        q = json.loads(line)
        filters = {**q["filters"], **({"section": sections} if sections else {})}
        res = post(a.url, {"query": q["query"], "filters": filters, "top_k": K, "rerank": True})
        passages = res["passages"]
        t = per_type[q["type"]]
        if q["type"] == "no_answer":
            t["rejected"].append(not passages)
            t["top"].append(max((p["score"] for p in passages), default=0.0))
            if passages:
                misses.append((q["id"], q["type"], q["query"], f"returned {len(passages)} passages, best {passages[0]['source']} p{passages[0]['page']} ({passages[0]['score']})"))
            continue
        r, rr, hit = score(passages, q["relevant_pages"], fixed)
        t["recall"].append(r); t["rr"].append(rr); t["hit"].append(hit)
        t["top"].append(max((p["score"] for p in passages), default=0.0))
        if not hit:
            misses.append((q["id"], q["type"], q["query"], f"got {[(p['source'][:14], p['page']) for p in passages[:3]]}  level={res['fallback_level']}"))

    mean = lambda x: round(sum(x) / len(x), 3) if x else None  # noqa: E731
    print(f"service: strategy={health.get('strategy')} hybrid={health.get('hybrid')} | sections={sections or 'all'} | k={K}")
    print(f"{'type':<12}{'n':>3} {'Recall@5':>9} {'MRR':>6} {'hit rate':>9}")
    answerable = {"recall": [], "rr": [], "hit": []}
    for typ in ("direct", "reworded", "ambiguous", "unknown_car"):
        t = per_type.get(typ)
        if not t:
            continue
        print(f"{typ:<12}{len(t['hit']):>3} {mean(t['recall']):>9} {mean(t['rr']):>6} {mean(t['hit']):>9}")
        for k in answerable:
            answerable[k] += t[k]
    na = per_type.get("no_answer")
    print(f"{'ALL answerable':<12}{len(answerable['hit']):>3} {mean(answerable['recall']):>9} {mean(answerable['rr']):>6} {mean(answerable['hit']):>9}")
    if na:
        print(f"no_answer correctly rejected: {sum(na['rejected'])}/{len(na['rejected'])}")
    if a.v:
        print("\nmisses:")
        for m in misses:
            print("  ", *m)
    if a.json:
        print(json.dumps({"strategy": health.get("strategy"), "hybrid": health.get("hybrid"), "sections": sections or "all",
                          "recall5": mean(answerable["recall"]), "mrr": mean(answerable["rr"]), "hit": mean(answerable["hit"]),
                          "rejected": f"{sum(na['rejected'])}/{len(na['rejected'])}" if na else None,
                          "direct_hit": mean(per_type["direct"]["hit"]), "reworded_hit": mean(per_type["reworded"]["hit"])}))


if __name__ == "__main__":
    main()
