# Transcript of a real run (plan B for the demo: show it on a slide if the live run fails)

What it is: the Telegram front talking to a **fake Telegram** (`n8n/telegram_front/mock_telegram.py`), with the real Agents 1, 2 and 3 running in n8n
(Agent 2 on local Ollama `qwen2.5:7b`). The sound was a **synthetic 6 s clip** (tone + noise), so Agent 1's label ("Démarrage normal") means nothing; a real engine
recording gives a real label. Run on 2026-10-17/18; about 2 minutes per pass. Nothing was edited except line wrapping.

```
USER > [voice note]
BOT  > Note vocale reçue. Quelle est la voiture ? (ex : Renault Clio 2015 diesel)
USER > Renault Clio 2015 diesel
BOT  > Décrivez le problème en une phrase (ex : le démarreur ne tourne pas, j'entends un clic).
USER > le démarreur ne tourne pas, j'entends un clic
BOT  > C'est noté. J'analyse le son, puis je consulte les manuels : cela peut prendre une à deux minutes…
BOT  > 🎧 Analyse du son : Démarrage normal (confiance medium, bruit dans la normale).
BOT  > Causes probables :
       • démarreur défectueux — 100 %
       Le démarreur pourrait être défectueux. Vérifiez si le clic se fait toujours lors de l'essai de démarrage. Si le clic persiste, essayez de
       démarrer le véhicule plusieurs fois de suite. [...] il est recommandé de faire vérifier le véhicule par un professionnel.
BOT  > Pour confirmer, quelques vérifications (moteur coupé, frein à main serré) :
BOT  > Vérification 1/2 : Est-ce que le clic se fait toujours lorsque vous essayez de démarrer le véhicule?
       (source : renault_clio4_2012-2016_fr.pdf, p. 225)            [Oui | Non | Je ne sais pas]
USER > [Non]
BOT  > Vérification 2/2 : Avez-vous essayé de démarrer le véhicule plusieurs fois de suite?
       (source : renault_clio4_2012-2016_fr.pdf, p. 225)            [Oui | Non | Je ne sais pas]
USER > [Non]
BOT  > Merci. Je mets à jour le diagnostic avec vos réponses…
```

An earlier run of the same conversation (Agent 1 had timed out on its first model load, so the bot said it relied on the description alone) ended with:

```
BOT  > Causes probables :
       • démarreur — 89 %   • batterie — 6 %   • cosses de batterie — 6 %
       Le démarreur semble être la cause la plus probable. Vérifiez d'abord si les cosses de batterie sont bien serrées et propres (p. 225). [...]
BOT  > 🔧 Guide de réparation (démarreur) :
       1. [How to Disconnect a Car or Truck Battery | Step 6: Step 6]  (source : ifixit_guide_66802, p. 6)
       2. Using a correctly sized wrench or socket, loosen the nut/bolt securing the cable on the '''negative''' battery terminal.  (source : ifixit_guide_66802, p. 6)
       3. Always disconnect the negative cable '''first''' (before removing the positive cable).  (source : ifixit_guide_66802, p. 6)
       ⚠️ Ensure engine is completely stopped and keys removed from ignition before starting work.
       ⚠️ Wear protective glasses and gloves.
BOT  > Limites : • Guide relies strictly on retrieved technical service documentation.
```

Be honest when showing it: the guide is generic and in English (Agent 3's corpus does not hold a fault-specific procedure for this car; see `NOTE_FOR_AGENT3.md`).
