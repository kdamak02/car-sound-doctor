# P2 — Clarification (one short question)

| | |
|---|---|
| Owner | B2 (written 2026-10-18 together with the Telegram front) |
| Used in | n8n workflow `telegram_front.json`, node "Handle update": when the front must ask again (car not understood, year or model missing, no problem described) |
| Model | Ollama `qwen2.5:7b`; if the answer is not one short French question (`checkQuestion`) the fixed question of the code is sent instead |
| Settings | temperature 0, plain text |
| Technique | role + rules + 2 examples; input is a small JSON: what is known, what is missing; output validated in code |
| Output contract | one sentence, one question mark, at most 25 words |

## Prompt (current version, v3)

```text
Tu es l'assistant Car Sound Doctor sur Telegram. L'utilisateur parle d'un problème de voiture mais son message ne suffit pas pour avancer.
On te donne en JSON ce qui est connu ("connu") et ce qui manque ("manquant").

Écris UNE seule question, en français, de 20 mots au maximum, qui demande uniquement ce qui manque.
Règles :
- une seule phrase interrogative, un seul point d'interrogation, pas de salutation, pas de remerciement, pas d'excuse ;
- ne redemande jamais ce qui est déjà connu ;
- si l'utilisateur doit choisir, donne les choix ou un court exemple entre parenthèses ;
- si rien n'est compris, demande la voiture et le problème dans la même question ;
- réponds par la question seule, sans guillemets.

- si l'enregistrement est inutilisable, demande d'en refaire un de 5 à 10 secondes près du moteur, sans parler (ne demande rien d'autre).

Exemples :
{"connu": {"make": "Kia"}, "manquant": "le modèle et l'année"} -> De quel modèle et de quelle année de Kia s'agit-il (ex : Rio 2016) ?
{"connu": {"make": "Opel", "model": "Corsa", "year": 2016}, "manquant": "le moment où le bruit apparaît"} -> Quand le bruit apparaît-il : au ralenti, au démarrage, au freinage ou en roulant ?
{"connu": {"make": "Fiat", "model": "500", "year": 2012}, "manquant": "un nouvel enregistrement : le dernier est illisible"} -> Pouvez-vous refaire un enregistrement de 5 à 10 secondes près du moteur, sans parler ?
```

## Input variables

| Variable | Source |
|---|---|
| `connu` | what the front already understood (make, model, year, symptom...) |
| `manquant` | text naming what is missing, set by the state machine |

## Test inputs

8 situations in `prompts/eval/run_p2_p7.py` (vehicle unknown, year missing, problem missing, moment missing, model missing, a greeting only, unusable recording, unclear answer). Rules checked by code, no judge model: exactly one question, at most 25 words, French,
asks for the missing item and for nothing else. Run: `python prompts/eval/run_p2_p7.py p2 v1 v2 v3 --show`.

## Versions

| Version | Date | Change | Why (observed failure) | Effect |
|---|---|---|---|---|
| v1 | 2026-10-18 | "Ask the user for the missing information." | baseline | **3/8** situations pass |
| v2 | 2026-10-18 | French, one question, 20 words, never re-ask what is known, choices in brackets, 2 examples | v1 | 8/8 on the first rules; with the stricter "new recording" rule **7/8**: asked a muddled double question |
| **v3** | 2026-10-18 | explicit rule and example for an unusable recording | v2 | **8/8** |

## Failure cases and before / after

1. **Two questions, English, rambling (v1).** "At what moment does the noise appear? Is it during idle, start-up, braking, or while driving?" (two question marks, English); "Bonjour! Comment allez-vous aujourd'hui ?" when nothing was understood; 36 words for the recording case. Fix: one question, 20 words, French, no greeting (v2).
2. **Confused new-recording request (v2).** "Avez-vous enregistré le bruit à nouveau, et combien de secondes d'enregistrement avez-vous cette fois-ci ?" Fix: a rule and an example ("Pouvez-vous refaire un enregistrement de 5 à 10 secondes près du moteur, sans parler ?"), v3.
3. **Safety net.** The text is used only if `checkQuestion` accepts it (one question mark, at most 25 words, no English words); otherwise the fixed question is sent. Tested in `n8n/telegram_front/test_telegram_front.js`.

## Failure to watch

8 situations, one model, rule-based checks only (the wording quality is read by hand on the printed answers). In the evaluation the question was never asked about something already known.
