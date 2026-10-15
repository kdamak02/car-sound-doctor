"""
Retrieval evaluation set for Agent 2 (owner B2): 30 French/English queries against the 6 owner's manuals.

    python agent2_diagnosis/tests/make_eval_queries.py        # writes rag/eval/queries.jsonl

How the labels were made: NOT with the retriever (that would be circular). Each expected passage was found by reading
the manual text (keyword search in the chunk index, then reading the chunk). `expect` below = (source prefix, page,
keyword that must appear in the chunk). The script resolves it to the chunk ids of the current "structure" index and
fails if a passage cannot be found, so a re-ingestion that moves things is noticed.

Fields (rag/eval/evaluate.py format, plus two extras):
  id, type, query, filters {make, model, year}, relevant [chunk_id]      <- used by rag/eval/evaluate.py
  relevant_pages [{source, page}]   strategy-independent (chunk ids differ between chunking strategies)
  note                              why the query exists / what to expect
Types: direct, reworded, ambiguous, unknown_car, no_answer (relevant = [] and nothing above the score threshold is wanted).
For unknown_car the car is absent from the corpus: any passage about the same fault in another car counts (fallback_level generic).
For ambiguous queries many passages are acceptable: read hit rate, not recall.
"""
import json
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "agent2_diagnosis/dev_rag/index/structure/chunks.jsonl"
OUT = ROOT / "rag/eval/queries.jsonl"

CLIO = {"make": "Renault", "model": "Clio", "year": 2015}
P208 = {"make": "Peugeot", "model": "208", "year": 2016}
P208II = {"make": "Peugeot", "model": "208", "year": 2021}
FIESTA = {"make": "Ford", "model": "Fiesta", "year": 2021}
FOCUS = {"make": "Ford", "model": "Focus", "year": 2023}
I10 = {"make": "Hyundai", "model": "i10", "year": 2025}

R, P1, P2, FI, FO, HY = "renault", "peugeot_208_2012", "peugeot_208_2019", "ford_fiesta", "ford_focus", "hyundai_i10"

# (type, query, car, expect=[(source prefix, page, keyword)], note)
SPEC = [
    ("direct", "Le démarreur ne tourne pas et les témoins du tableau de bord faiblissent", CLIO, [(R, 225, "demarreur ne tourne pas")], "Clio troubleshooting table p.225"),
    ("reworded", "Ma voiture ne démarre pas, j'entends juste un clic quand je tourne la clé", CLIO, [(R, 225, "demarreur ne tourne pas")], "same passage, driver wording"),
    ("direct", "Le témoin de température du liquide de refroidissement est passé au rouge", CLIO, [(R, 64, "temperature du liquide"), (R, 227, "le moteur chauffe")], "warning light page + troubleshooting table"),
    ("reworded", "Mon moteur chauffe, ça bouillonne dans le bocal de refroidissement", CLIO, [(R, 227, "bouillonnement")], "overheating row of the table"),
    ("direct", "Le témoin de pression d'huile s'allume en virage", CLIO, [(R, 226, "en virage")], "oil pressure row, p.226"),
    ("direct", "De la fumée blanche sort de l'échappement de ma Clio diesel", CLIO, [(R, 226, "fumee blanche")], "white smoke = DPF regeneration"),
    ("reworded", "La direction est devenue très dure tout d'un coup", CLIO, [(R, 227, "direction devient dure")], "power steering overheating row"),
    ("direct", "Les clignotants ne fonctionnent plus", CLIO, [(R, 228, "clignotants")], "fuse / bulb rows"),
    ("direct", "La carte Renault ne verrouille plus les portes", CLIO, [(R, 224, "carte renault ne fonctionne pas")], "key card, p.224"),
    ("direct", "Que faire si je tombe en panne de carburant avec mon diesel ?", CLIO, [(R, 103, "panne de carburant")], "section symptoms"),
    ("direct", "Comment démarrer avec la batterie d'un autre véhicule ?", CLIO, [(R, 217, "batterie d")], "procedure in a 'maintenance' section: tests the section filter of Agent 2"),
    ("direct", "Le témoin de charge de la batterie est allumé en permanence", P208, [(P1, 14, "charge batterie")], "Peugeot warning table, cause + actions"),
    ("direct", "Le témoin d'autodiagnostic moteur clignote", P208, [(P1, 15, "autodiagnostic")], "Peugeot warning table"),
    ("reworded", "Le voyant des pneus dégonflés est allumé et ça fait un bip", P208, [(P1, 17, "sous-gonflage")], "TPMS row"),
    ("reworded", "Il n'y a presque plus d'AdBlue, que va-t-il se passer ?", P208, [(P1, 21, "adblue")], "SCR / BlueHDi row"),
    ("direct", "Message défaut antipollution SCR, risque de blocage du démarrage", P208II, [(P2, 25, "scr")], "208 II SCR table"),
    ("direct", "Le témoin de température de liquide de refroidissement du moteur s'allume pendant que je roule", FIESTA, [(FI, 115, "temperature de liquide de refroidissement")], "Fiesta warning lights"),
    ("reworded", "Le voyant de pression des pneus reste allumé en continu", FIESTA, [(FI, 372, "sous-gonfles")], "Fiesta TPMS table"),
    ("direct", "Le témoin d'anomalie de fonctionnement du moteur clignote", FIESTA, [(FI, 115, "anomalie de fonctionnement"), (FI, 171, "anomalie de fonctionnement")], "engine light, two pages"),
    ("direct", "Le voyant de pression des pneus clignote puis reste allumé", FOCUS, [(FO, 501, "clignotement")], "Focus TPMS table"),
    ("direct", "L'assistant pré-collision est indisponible, capteur bloqué", FOCUS, [(FO, 382, "capteur bloque"), (FO, 382, "calandre")], "Focus driver-assist table"),
    ("direct", "The malfunction indicator lamp is on, can I keep driving?", I10, [(HY, 110, "malfunction indicator lamp")], "English manual"),
    ("ambiguous", "Un voyant orange s'est allumé au tableau de bord", P208, [(P1, None, "temoin etat cause")], "any warning-light table of the 208: read hit rate"),
    ("ambiguous", "Il y a un problème avec mon moteur", CLIO, [(R, 225, "demarreur"), (R, 226, "pression"), (R, 227, "moteur chauffe")], "vague: several tables are acceptable"),
    ("unknown_car", "Le témoin de température du liquide de refroidissement est rouge", {"make": "Kia", "model": "Rio", "year": 2018},
     [(R, 64, "temperature du liquide"), (R, 227, "le moteur chauffe"), (FI, 115, "temperature de liquide de refroidissement")], "Kia is not in the corpus: generic fallback, same fault in other cars"),
    ("unknown_car", "Le voyant de pression des pneus est allumé", {"make": "Toyota", "model": "Corolla", "year": 2016},
     [(P1, 17, "sous-gonflage"), (FI, 372, "sous-gonfles"), (FO, 501, "clignotement"), (R, 114, "gonfler pneus")], "Toyota is not in the corpus"),
    ("no_answer", "Grincement de la courroie d'accessoires au démarrage à froid", CLIO, [], "the mock-up scenario: owner's manuals have no belt-noise diagnosis"),
    ("no_answer", "Comment remplacer la courroie de distribution ?", P208, [], "no timing-belt procedure in an owner's manual"),
    ("no_answer", "Bruit de roulement de roue qui augmente avec la vitesse", FIESTA, [], "wheel-bearing noise is not covered"),
    ("no_answer", "Quelle est la meilleure recette de pizza ?", {}, [], "off-topic"),
]


def norm(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().replace("-", " ")


def main():
    rows = [json.loads(line) for line in INDEX.read_text(encoding="utf-8").splitlines()]
    out = []
    for i, (typ, query, car, expect, note) in enumerate(SPEC, 1):
        relevant, pages = [], []
        for prefix, page, keyword in expect:
            hits = [r for r in rows if r["source"].startswith(prefix) and (page is None or r["page"] == page) and norm(keyword.replace("-", " ")) in norm(r["text"])]
            if page is None:  # a whole family of passages: only the diagnosis tables of that manual
                hits = [r for r in hits if r["section"] == "causes"]
            if not hits:
                raise SystemExit(f"q{i:02d}: expected passage not found: {prefix} p{page} {keyword!r}")
            for r in hits:
                if r["chunk_id"] not in relevant:
                    relevant.append(r["chunk_id"])
                    pages.append({"source": r["source"], "page": r["page"]})
        out.append({"id": f"q{i:02d}", "type": typ, "query": query, "filters": car, "relevant": relevant, "relevant_pages": pages, "note": note})
    OUT.write_text("\n".join(json.dumps(o, ensure_ascii=False) for o in out) + "\n", encoding="utf-8")
    from collections import Counter
    print(len(out), "queries ->", OUT.relative_to(ROOT), dict(Counter(o["type"] for o in out)))
    for o in out:
        print(f"  {o['id']} {o['type']:<11} {len(o['relevant'])} relevant  {o['query'][:70]}")


if __name__ == "__main__":
    main()
