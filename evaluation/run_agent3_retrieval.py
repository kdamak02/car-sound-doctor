"""Retrieval evaluation of Agent 3's own RAG (agent3_repair/rag: Qdrant + BM25 hybrid + reranker), written by B2 because none was in the repository.

    python evaluation/run_agent3_retrieval.py            (needs Agent 3 on :8003)

Test set = the 16 cases of agent3_repair/tests/agent3_test_cases.json: each has a case record and the sources that were prepared for it by group C
(`retrieved_sources`). For every case with expected sources we call POST /search (top_k 5) and measure: hit (at least one expected source returned),
recall@5 (share of expected sources returned), and the rank of the first expected source (for MRR). The expected sources were chosen by the authors of the cases,
not by an independent labeller, so the figures are indicative.
"""
import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
cases = json.loads((ROOT / "agent3_repair/tests/agent3_test_cases.json").read_text("utf-8"))
rows = []
for c in cases:
    expected = {s["source_id"] for s in c.get("retrieved_sources") or []}
    if not expected:
        continue
    try:
        r = requests.post("http://127.0.0.1:8003/search", json={"case_record": c["case_record"], "top_k": 5, "allow_generic": True}, timeout=300)
        got = [s.get("source_id") for s in r.json().get("retrieved_sources", [])]
    except (requests.RequestException, ValueError) as e:
        sys.exit(f"Agent 3 /search failed: {e}")
    first = next((i + 1 for i, g in enumerate(got) if g in expected), None)
    rows.append((c["case_id"], c["name"], len(expected), len(expected & set(got)), first))
    print(f"{c['case_id']:6} {c['name'][:42]:42} expected {len(expected)}  found {len(expected & set(got))}  first rank {first}")
GOLD = {"TC-01", "TC-02", "TC-08", "TC-11", "TC-12", "TC-13"}  # cases whose expected source is the right procedure for the vehicle; the others are validator fixtures
# (wrong-vehicle manual, fabricated source, critical brake issue...): their `retrieved_sources` are not retrieval targets
for name, sel in (("retrieval cases (6)", [r for r in rows if r[0] in GOLD]), ("all 15 fixtures (indicative only)", rows)):
    n = len(sel)
    print(f"\n{name}: hit rate {sum(1 for r in sel if r[3] > 0)}/{n}, recall@5 {round(sum(r[3] / r[2] for r in sel) / n, 3)}, MRR {round(sum(1 / r[4] for r in sel if r[4]) / n, 3)}")
