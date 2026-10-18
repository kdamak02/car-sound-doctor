# Note pour le groupe A (Agent 1) : ce que j'ai observé en branchant l'Agent 1 sur la chaîne

Merci pour le service : il tourne en mode réel (`"mock": false`), accepte les notes vocales OGG/Opus, répond `unusable_audio` sous 1,5 s et HTTP 400 sur un fichier vide. Trois choses à connaître avant la soutenance.

## 1. Un enregistrement de voix n'est pas rejeté
Scénario du cahier des charges (`evaluation/scenarios.csv`, s03 : "speech only: expect unusable_audio"). J'ai envoyé une phrase parlée (CREMA-D, `1001_DFA_NEU_XX.wav`, convertie en OGG Opus) avec `context=startup` :
`{"status": "ok", "label": "normal_startup", "confidence": "medium", "anomaly_score": 0.981, "warnings": [], "description": {"speech_ratio": 0.0, ...}}`.
`speech_ratio` vaut 0.0 pour une personne qui parle, donc aucune règle ne peut la rejeter, et l'Agent 2 reçoit une "analyse du son" sans valeur. Piste : un détecteur de parole (par exemple l'énergie dans 300-3400 Hz et la modulation syllabique, ou un petit modèle VAD) dans `speech_ratio`, et `unusable_audio` + avertissement au-dessus d'un seuil.

## 2. La compression Telegram change la classe (déjà connu, voici des chiffres)
Clips publics `jlacsam/cardiag` (copies du jeu Kaggle, donc des données vues à l'entraînement), `context=startup`, WAV puis OGG Opus 16 à 64 kbit/s :
- `sample_failed_start_0002/0003/0004` : `bad_ignition` 80-100 % à tous les débits (stable).
- `sample_failed_start_0000/0001` : WAV -> `dead_battery` 52 % (medium) ; OGG Opus -> `normal_startup` 48 à 91 %, parfois avec confiance `high`. Un démarrage raté est alors annoncé normal.
Ce sont des clips de 1,5 s (le minimum accepté) ; les utilisateurs réels enverront 5 à 10 s.

## 3. Première analyse lente
Après un démarrage du service, la première analyse prend 50 à 100 s (chargement du modèle AST) ; les suivantes 4 s. Côté n8n le délai de l'appel est de 90 s ; un préchauffage au démarrage (une analyse factice) éviterait le piège.

Pour reproduire : `python evaluation/run_scenarios.py s03` (voir `evaluation/run_scenarios.py`).
