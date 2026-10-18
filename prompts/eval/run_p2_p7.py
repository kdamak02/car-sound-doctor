"""Evaluation of prompts P2 (clarification question) and P7 (short final message).

    python prompts/eval/run_p2_p7.py p2 v1 v2 [--show]      |      python prompts/eval/run_p2_p7.py p7 v1 v2 [--show]

Each run asks the model (Ollama, temperature 0) once per situation and checks the answer with rules, no judge model:
 P2: exactly one question, at most 25 words, French, asks for the missing item and only for it
 P7: at most 450 characters, plain text (no markdown, no emoji: it may be read aloud), French, every number comes from the input,
     says to stop driving when safety_stop, sends to a professional when the diagnosis is not firm or the guide says so
"""
import json
import re
import sys
from pathlib import Path
from urllib import request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from p2_p7_prompts import PROMPTS  # noqa: E402

MODEL = "qwen2.5:7b"
FRENCH_STOP = re.compile(r"\b(please|the|your|what|which|could you|car)\b", re.I)

# (id, what we know, what is missing, regex the question must match, regex it must NOT match)
P2_CASES = [
    ("vehicle", {}, "la marque, le modèle et l'année du véhicule", r"marque|mod[èe]le|voiture|v[ée]hicule", r"bruit|symptôme|quand"),
    ("year", {"make": "Renault", "model": "Clio"}, "l'année du véhicule", r"ann[ée]e", r"marque|mod[èe]le|bruit"),
    ("symptom", {"make": "Renault", "model": "Clio", "year": 2015}, "la description du problème (que se passe-t-il ?)", r"probl[èe]me|bruit|se passe|constat|observ|entend|d[ée]crire|d[ée]cri", r"ann[ée]e|marque"),
    ("context", {"make": "Peugeot", "model": "208", "year": 2019, "symptom": "grincement"}, "le moment où le bruit apparaît (ralenti, démarrage, freinage, en roulant)", r"quand|moment|ralenti|d[ée]marr|frein|roulant", r"ann[ée]e|marque|mod[èe]le"),
    ("model", {"make": "Peugeot"}, "le modèle du véhicule (le constructeur est connu)", r"mod[èe]le", r"ann[ée]e|bruit|quand"),
    ("nothing", {}, "toute information : le message ne contient ni voiture ni problème (c'est une salutation)", r"voiture|v[ée]hicule|probl[èe]me|marque|aider", r"^$"),
    ("recording", {"make": "Ford", "model": "Fiesta", "year": 2020}, "un nouvel enregistrement : le dernier fait moins d'une seconde", r"enregistr.*secondes|secondes.*enregistr|refai.*(enregistr|vocal)", r"ann[ée]e|marque|mod[èe]le|combien|avez-vous"),
    ("answer", {"question": "Les cosses de la batterie sont-elles propres et serrées ?"}, "une réponse claire : le conducteur a répondu « peut-être », il faut oui, non ou « je ne sais pas »", r"oui|non|sais pas|cosses|batterie", r"ann[ée]e|marque"),
]

# (id, input the model receives, must stop driving, must send to a professional)
P7_CASES = [
    ("starter", {"cause": "démarreur", "probabilite_pct": 95, "conseil": "Faites tester la batterie ou le démarreur chez un garagiste (p. 225).", "ne_pas_rouler": False, "guide": "guide_available", "son": "Allumage défaillant"}, False, False),
    ("oil", {"cause": "pression d'huile basse", "probabilite_pct": 70, "conseil": "Vérifiez le niveau d'huile (p. 226).", "ne_pas_rouler": True, "guide": "professional_assistance_required", "son": "Manque d'huile"}, True, True),
    ("unsure", {"cause": "batterie déchargée", "probabilite_pct": 45, "conseil": "Testez la batterie.", "ne_pas_rouler": False, "guide": "aucun", "son": "Batterie faible", "diagnostic_ferme": False}, False, True),
    ("manual_silent", {"cause": None, "probabilite_pct": None, "conseil": "Les manuels fournis ne contiennent rien de fiable sur ce symptôme.", "ne_pas_rouler": False, "guide": "aucun", "son": None}, False, True),
    ("charge", {"cause": "alternateur", "probabilite_pct": 62, "conseil": "Faites contrôler l'alternateur et la batterie (p. 14).", "ne_pas_rouler": False, "guide": "diagnosis_only", "son": None}, False, False),
    ("brakes", {"cause": "plaquettes de frein usées", "probabilite_pct": 80, "conseil": "Faites contrôler les freins.", "ne_pas_rouler": True, "guide": "professional_assistance_required", "son": "Freins usés"}, True, True),
]


def ask(system, user):
    body = json.dumps({"model": MODEL, "stream": False, "options": {"temperature": 0, "num_ctx": 4096},
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
    req = request.Request("http://localhost:11434/api/chat", data=body, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())["message"]["content"].strip()


def check_p2(text, want, avoid):
    problems = []
    if text.count("?") != 1:
        problems.append(f"{text.count('?')} question marks")
    if len(text.split()) > 25:
        problems.append(f"{len(text.split())} words")
    if FRENCH_STOP.search(text):
        problems.append("not French")
    if not re.search(want, text, re.I):
        problems.append("does not ask for the missing item")
    if avoid != r"^$" and re.search(avoid, text, re.I):
        problems.append("asks for something else too")
    return problems


def check_p7(text, data, stop, pro):
    problems = []
    if len(text) > 450:
        problems.append(f"{len(text)} chars")
    if re.search(r"[*#_`•]|[\U0001F300-\U0001FAFF☀-➿]", text):
        problems.append("markdown or emoji")
    if FRENCH_STOP.search(text):
        problems.append("not French")
    allowed = set(re.findall(r"\d+", json.dumps(data, ensure_ascii=False)))
    extra = [n for n in re.findall(r"\d+", text) if n not in allowed]
    if extra:
        problems.append("invented numbers " + ",".join(extra))
    if stop and not re.search(r"ne roulez pas|ne conduisez pas|n.utilisez pas|arr[êe]tez|immobilis", text, re.I):
        problems.append("does not say to stop driving")
    if pro and not re.search(r"m[ée]canicien|professionnel|garag", text, re.I):
        problems.append("does not send to a professional")
    if re.search(r"continu(ez|er) [àa] rouler|vous pouvez (continuer|rouler|conduire)|pas de danger|sans risque", text, re.I):
        problems.append("invents permission to drive")
    return problems


which, versions = sys.argv[1], [a for a in sys.argv[2:] if not a.startswith("--")]
for v in versions:
    system = PROMPTS[which][v]
    ok, bad = 0, []
    cases = P2_CASES if which == "p2" else P7_CASES
    for case in cases:
        if which == "p2":
            cid, have, missing, want, avoid = case
            out = ask(system, json.dumps({"connu": have, "manquant": missing}, ensure_ascii=False))
            problems = check_p2(out, want, avoid)
        else:
            cid, data, stop, pro = case
            out = ask(system, json.dumps(data, ensure_ascii=False))
            problems = check_p7(out, data, stop, pro)
        ok += not problems
        if problems:
            bad.append((cid, problems, out))
        if "--show" in sys.argv:
            print(f"  [{v}] {cid}: {'OK ' if not problems else 'BAD'} {out[:200]!r} {problems or ''}")
    print(f"== {which} {v}: {ok}/{len(cases)} situations pass all checks")
    for cid, problems, out in bad:
        print(f"   {cid}: {problems}")
