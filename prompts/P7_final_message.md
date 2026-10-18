# P7 — Final message (short, plain text, to be read aloud)

| | |
|---|---|
| Owner | B2 (written 2026-10-18 together with the Telegram front) |
| Used in | n8n workflow `telegram_front.json`, node "Reply": one extra message "🗣️ En résumé" after the detailed answer. A speech synthesis step (the architecture's Piper TTS) is **not built**: the text is ready for it |
| Model | Ollama `qwen2.5:7b`; the text is sent only if it passes `checkSummary`, else a fixed template (`templateSummary`) is sent |
| Settings | temperature 0, plain text |
| Technique | role + numbered rules + 1 example + input as JSON; **code guardrail** on the output (length, no markdown, French, no invented number, stop-driving and see-a-professional rules) |
| Output contract | at most 3 short sentences, at most 450 characters |

## Prompt (current version, v4)

```text
Tu es la voix de Car Sound Doctor. Tu reçois en JSON le résultat d'une analyse de voiture : cause, probabilite_pct, conseil, ne_pas_rouler, guide, son, diagnostic_ferme.
Écris un résumé pour le conducteur : 3 phrases courtes au maximum, en français, en texte brut qui sera lu à voix haute (pas de markdown, pas d'emoji, pas de liste), 450 caractères au maximum.

Règles :
1. Commence par la cause la plus probable et son pourcentage, seulement s'ils sont connus. Si la cause est inconnue, dis que les manuels ne permettent pas de conclure.
2. Reprends le conseil du JSON. N'ajoute aucun fait, aucune cause et aucun chiffre qui ne soient pas dans le JSON.
3. Si ne_pas_rouler est vrai, dis clairement de ne pas rouler avec le véhicule.
4. Termine en recommandant de consulter un mécanicien ou un professionnel quand guide vaut professional_assistance_required, quand diagnostic_ferme est faux, ou quand la cause est inconnue.
5. ne_pas_rouler = false ne veut PAS dire que l'on peut rouler : cela veut dire que tu ne dis RIEN sur la conduite (pas un mot sur rouler, conduire ou prudence).
   Ne donne JAMAIS de consigne de conduite qui n'est pas dans le JSON : ne dis pas de continuer à rouler, de rouler prudemment ni qu'il n'y a pas de danger. La seule consigne de conduite permise est de ne pas rouler (quand ne_pas_rouler est vrai).
Réponds par le résumé seul.

Exemple :
{"cause": "pompe à eau", "probabilite_pct": 70, "conseil": "Faites contrôler le circuit de refroidissement (p. 64).", "ne_pas_rouler": true, "guide": "professional_assistance_required", "son": null} ->
La cause la plus probable est la pompe à eau, à 70 %. Faites contrôler le circuit de refroidissement (page 64). Ne roulez pas avec le véhicule et consultez un mécanicien.
```

## Input variables

| Variable | Source |
|---|---|
| JSON `{cause, probabilite_pct, conseil, ne_pas_rouler, guide, son, diagnostic_ferme}` | built from the final Agent 2 answer (top cause, first two sentences of its advice, `safety_stop`, `conclusive`), Agent 3's guide status and Agent 1's class |

## Test inputs

6 situations in `prompts/eval/run_p2_p7.py` (starter, oil pressure with stop-driving, unsure diagnosis, manuals silent, charge light, brakes). Checks by code: at most 450 characters, no markdown or emoji, French, every number comes from the input,
"ne roulez pas" when `ne_pas_rouler`, "mécanicien/professionnel" when the diagnosis is not firm or the guide says so, no permission to drive. Run: `python prompts/eval/run_p2_p7.py p7 v1 v2 v3 v4 --show`.

## Versions

| Version | Date | Change | Why (observed failure) | Effect |
|---|---|---|---|---|
| v1 | 2026-10-18 | "Summarize the diagnosis for the user." | baseline | **0/6**: English answers, no "stop driving", no professional advice |
| v2 | 2026-10-18 | French, 3 sentences, plain text, 4 rules, 1 example | v1 | 6/6 on the first rules; after adding the check "no invented permission to drive" **5/6** |
| v3 | 2026-10-18 | rule: never give driving advice that is not in the JSON | v2 kept "Continuez à rouler avec prudence" when the manuals were silent | 5/6 (same case) |
| v4 | 2026-10-18 | says what `ne_pas_rouler = false` means (the model read it as permission) | v3 | 5/6 (same case): the 7B model does not obey this rule |
| **v4 + guardrail** | 2026-10-18 | `checkSummary` rejects the message, `templateSummary` is sent instead | prompt rules alone are not enough for a small model | the unsafe text never reaches the driver: 6/6 situations end with a message that passes every rule (1 of 6 uses the template) |

## Failure cases and before / after

1. **English (v1).** "The diagnosis indicates a likely issue with the starter (95% probability)..." Fix: French in the first line of the prompt and a French example (v2).
2. **Missing safety (v1).** For the oil-pressure and brake cases nothing said to stop driving. Fix: rule 3 (v2); checked by code.
3. **Invented advice (v2-v4).** With the manuals silent the model adds "Continuez à rouler avec prudence", which is not in its input and is not a safe thing to say. Three prompt versions did not remove it from this 7B model. Fix: a validator in code that refuses the text and a deterministic fallback. This is the guardrails technique: the prompt asks, the code enforces.
4. **Numbers.** Any digit that is not in the input JSON rejects the text (no invented probability).

## Failure to watch

6 situations, one model, rule-based checks. Not tested with a speech synthesiser. The template is plain but always correct.
