# Note pour Amir (Agent 3) : ce que j'ai observé en branchant l'Agent 2 sur ton service

Salut Amir. J'ai branché l'Agent 2 sur ton `/generate` (branche `integration/agent2-agent3`, mapper `agent2_diagnosis/to_agent3.py`)
et lancé la chaîne complète plusieurs fois (Agent 1 -> Agent 2 -> Agent 3). Ton service est stable et ses guides passent ta validation.
Voici ce qui m'a gêné, avec les preuves. Rien n'est modifié dans ton dossier.

## 1. Les guides ne sont pas liés à la panne (le plus important)
- Cas Clio qui ne démarre pas (Agent 2 conclut "batterie déchargée" puis "démarreur défectueux"). Résultat : `guide_available`, `validation.valid = true`,
  mais les 3 étapes viennent toutes de `ifixit_guide_66802` ("How to Disconnect a Car or Truck Battery"). C'est une procédure de débranchement,
  pas un remplacement de batterie ni de démarreur. Le champ `fault.name` est bien "démarreur" / "batterie déchargée", donc le guide ne correspond pas à la panne annoncée.
- L'étape 1 est un titre brut : `[How to Disconnect a Car or Truck Battery | Step 6: Step 6]`.
- Cas pression d'huile (Clio) : sources récupérées = `ifixit_guide_66802`, `repair_tsb_generic_rattle_squeak_guide`, `ifixit_guide_150417`. Aucune ne parle d'huile.
  Ton service répond `professional_assistance_required` (c'est le bon réflexe), mais les sources ne sont pas pertinentes.
- Pistes : (a) une porte de pertinence avant de générer (si aucune source ne parle de la panne -> `professional_assistance_required` ou `diagnosis_only`, jamais `guide_available`) ;
  (b) du contenu spécifique Renault/Peugeot pour les 3 pannes de démo (batterie, démarreur, témoin de charge/alternateur) ; (c) un test dans `validate_agent3` qui vérifie que la source parle du `fault.name`.

## 2. Avertissement de sécurité faux
Cas pression d'huile : `safety_warnings` contient "Critical brake issue detected. Do not drive the vehicle until assessed". Il n'y a aucun frein dans ce dossier
(cause = "pression d'huile basse", classe audio `low_oil`). On dirait une règle de sécurité qui se déclenche par mot-clé ou par défaut. À vérifier dans tes règles de sécurité.

## 3. Langue
Le dossier envoyé a `language = "fr"`. Dans une exécution, les étapes et les limites sont en anglais ("Guide relies strictly on retrieved technical service documentation.") ;
dans une autre elles sont en français. Pour Telegram il faut du français à chaque fois.

## 4. Contrat d'entrée : peux-tu confirmer ces choix ?
Le mapper met : `audio.class` = l'identifiant de classe de l'Agent 1 (`dead_battery`, `low_oil`, `alternator`... tel que dans `contracts/labels.json`, pas `belt_squeal`),
`audio.confidence` = la probabilité de cette classe, `vehicle.engine` = le carburant (`diesel`...), `diagnosis.status` = `confirmed` si l'Agent 2 est conclusif,
`inconclusive` s'il n'y a plus de vérification possible, sinon `probable`. Les `evidence` sont nettoyées des identifiants de passage. `checks` = question + réponse du conducteur
("Not checked" si pas de réponse). Si ton modèle attend d'autres valeurs (par exemple des noms de classes différents), dis-le-moi et j'ajuste `to_agent3.py` (tests dans `agent2_diagnosis/tests/test_to_agent3.py`).

## 5. Dépôt et quotas
- `agent3_repair/data/qdrant_db/.../storage.sqlite` est versionné (≈ 90 Mo de données au total) ET modifié à chaque lancement du service : `git status` est toujours sale
  et ça bloque les changements de branche tant que le service tourne. Idée : l'ignorer et reconstruire l'index au premier lancement (ton README dit déjà qu'il se construit depuis `rag_chunks.json`).
- Groq (clé partagée) : 8 000 tokens/minute et 200 000 tokens/jour **par modèle** pour toute l'organisation. J'ai épuisé `openai/gpt-oss-120b` en tests (ton service utilise
  `qwen/qwen3.8-27b`, donc pas touché). Le jour de la démo : pas de séries de tests, et garde un plan B si le quota de ton modèle saute.

## Pour reproduire (5 minutes)
```powershell
.\agent2_diagnosis\start_full_chain.ps1 -Python "<python>" -RagDevice cpu     # services (voir agent2_diagnosis/HANDOVER.md)
python agent2_diagnosis/tests/chain_to_agent3.py clio_starter_pass2            # Agent 2 -> mapper -> ton /generate, affiche étapes, sources, validation
python agent2_diagnosis/tests/chain_to_agent3.py clio_oil_pressure_pass1       # le cas de l'avertissement "brake"
```
Merci ! Si tu veux, je regarde avec toi la porte de pertinence (point 1), c'est ce qui compterait le plus pour la soutenance.
