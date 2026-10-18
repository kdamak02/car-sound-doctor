"""Does the symptom that prompt P1 extracts help or hurt the retrieval? (needs Ollama and the search service on :8002)

    python prompts/eval/run_p1_retrieval.py

For 8 lay messages about the Renault Clio 4 (the manual page that answers each one is known), the query sent to /search is: the whole message (raw),
the `symptom` of P1 v2, the `symptom` of P1 v3 (manual wording). Hit = the expected page is among the 3 passages returned.
"""
import json
import sys
import time
from pathlib import Path
from urllib import request

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from p1_prompts import PROMPTS  # noqa: E402

FILTERS = {"make": "Renault", "model": "Clio", "year": 2015, "section": ["symptoms", "causes", "codes", "maintenance"]}
CASES = [  # (message, pages of renault_clio4_2012-2016_fr.pdf that answer it)
    ("Ma Clio 2015 diesel ne démarre pas, j'entends juste un clic", {225}),
    ("Clio 2015, le voyant d'huile s'allume dans les virages", {63, 226}),
    ("Clio 2015 la direction est devenue très dure d'un coup", {227}),
    ("Ma Clio 2015 chauffe et le voyant de température est rouge", {64, 227}),
    ("Clio 2015 diesel, de la fumée blanche sort de l'échappement", {226}),
    ("Sur ma Clio 2015 les clignotants ne marchent plus", {228}),
    ("Ma Clio 2015 : la carte ne verrouille plus les portes", {224}),
    ("Clio 2015, comment démarrer avec la batterie d'un autre véhicule ?", {217}),
]


def p1_fields(version, text):
    for _ in range(3):
        try:
            body = json.dumps({"model": "qwen2.5:7b", "stream": False, "format": "json", "options": {"temperature": 0, "num_ctx": 4096},
                               "messages": [{"role": "system", "content": PROMPTS[version]}, {"role": "user", "content": text}]}).encode()
            r = request.urlopen(request.Request("http://localhost:11434/api/chat", data=body, headers={"Content-Type": "application/json"}), timeout=300)
            return json.loads(json.loads(r.read())["message"]["content"])
        except Exception:  # the model may still be loading
            time.sleep(5)
    return None


def hit(query, pages):
    r = requests.post("http://localhost:8002/search", json={"query": query, "filters": FILTERS, "top_k": 3, "rerank": True}, timeout=120).json()
    got = {p["page"] for p in r["passages"] if "clio4" in p["chunk_id"]}
    return bool(got & pages)


tot = {"raw": 0, "v2": 0, "v3": 0, "v4 either": 0, "v4 both in one query": 0}
for text, pages in CASES:
    f2, f3, f4 = (p1_fields(v, text) or {} for v in ("v2", "v3", "v4"))
    s2, s3, s4, m4 = f2.get("symptom"), f3.get("symptom"), f4.get("symptom"), f4.get("manual_symptom")
    row = {"raw": hit(text, pages), "v2": hit(s2 or text, pages), "v3": hit(s3 or text, pages),
           "v4 either": hit(s4 or text, pages) or hit(m4 or text, pages), "v4 both in one query": hit(f"{m4 or ''}. {s4 or text}".strip(". "), pages)}
    for k in tot:
        tot[k] += row[k]
    print(" ".join(f"{k}={int(v)}" for k, v in row.items()), f"| v4: {s4!r} / {m4!r}")
print(f"\nhit@3 over {len(CASES)} messages:", tot)
