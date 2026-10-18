# Car Sound Doctor — installation et exécution

Ce guide permet d'installer et de lancer la chaîne complète sur un PC Windows :
Telegram → n8n → Agent 1 (audio) → Agent 2 (diagnostic, LLM + RAG) → Agent 3 (guide de réparation).
Il a été suivi de bout en bout sur un PC portable Windows 11 le 18 octobre 2026.

- Dépôt : https://github.com/kdamak02/car-sound-doctor (branche `integration/agent2-agent3`)
- Workflows n8n exportés : `n8n/workflows/full_chain_groq.json` (chaîne des 3 agents) et `n8n/workflows/telegram_front.json` (bot Telegram)
- Architecture détaillée : `ARCHITECTURE.md` · prompts : `prompts/PROMPT_LIBRARY.md`

## 1. Prérequis

| Élément | Version / remarque |
|---|---|
| Windows 10 ou 11 | 16 Go de RAM conseillés, 15 Go de disque libre (modèles + environnements) |
| Python | 3.11, coché « Add to PATH » à l'installation |
| Docker Desktop | lancé avant toute commande (n8n tourne dans Docker) |
| Git | pour cloner le dépôt |
| Clé Groq | gratuite sur console.groq.com ; utilisée par l'Agent 2 et l'Agent 3 |
| Bot Telegram | créé avec @BotFather (`/newbot`), on garde le token |
| Index RAG des manuels | dossier `agent2_diagnosis/dev_rag/index/` fourni par le groupe B (les manuels PDF des constructeurs ne sont pas redistribués) |
| Ollama (facultatif) | seulement pour les prompts P1/P2/P7 du bot ; sans lui, le bot utilise ses replis en code |

Les clés ne sont jamais écrites dans le dépôt : elles vont dans des fichiers `.env` exclus de Git.

## 2. Installation (une seule fois)

Dans PowerShell, depuis le dossier où l'on veut le projet :

```powershell
git clone https://github.com/kdamak02/car-sound-doctor.git
cd car-sound-doctor
git checkout integration/agent2-agent3
```

Copier le dossier d'index fourni par le groupe B dans `agent2_diagnosis\dev_rag\index\` (il doit contenir `structure`, `obdex` et `fixed`).

Puis lancer le script d'installation, avec le token du bot et la clé Groq :

```powershell
.\scripts\setup_demo_pc.ps1 -TelegramToken "<token BotFather>" -GroqKey "<clé Groq>"
```

Le script peut être relancé sans risque : chaque étape déjà faite est sautée.

| Étape | Ce qu'elle fait |
|---|---|
| 1 | vérifie Python, Docker et Ollama |
| 2 | crée `.venv-agent1` (PyTorch CPU, Transformers, onnxruntime) pour l'Agent 1 |
| 3 | crée `.venv-chain` (RAG, Agent 3, page de test) |
| 4 | écrit `.env` (token Telegram, clé de chiffrement n8n) et `agent3_repair\.env` (clé Groq), en UTF-8 sans BOM |
| 5 | prépare les clips de démonstration dans `data\audio\demo_clips\` |
| 6 | vérifie l'index RAG (ou le reconstruit si les PDF sont présents : 20 à 60 min) |
| 7 | télécharge une fois les modèles AST, BGE-M3 et bge-reranker-v2-m3 |
| 8 | télécharge `qwen2.5:7b` si Ollama est installé |
| 9 | lance les tests hors ligne (Agent 1, Agent 2, nœuds n8n, front Telegram) |

À la fin, le script affiche « Everything is ready » ou la liste de ce qui reste à faire.

## 3. Lancer les services (à chaque fois)

Docker Desktop doit être lancé. Dans PowerShell, à la racine du dépôt :

```powershell
.\agent2_diagnosis\start_full_chain.ps1 -Python "$PWD\.venv-chain\Scripts\python.exe" -NoOllama
```

(Retirer `-NoOllama` si Ollama est installé.) Le script démarre les services et affiche leur état ; le premier démarrage de l'Agent 1 prend environ une minute (préchauffage du modèle).

| Service | Adresse |
|---|---|
| n8n | http://localhost:5678 |
| Agent 1, audio (FastAPI) | http://localhost:8001/docs |
| RAG de l'Agent 2 (/search) | http://localhost:8002/docs |
| Agent 3, réparation (FastAPI) | http://localhost:8003/docs |
| Page de test des 3 agents (Streamlit) | http://localhost:8501 |

## 4. Configurer n8n (une seule fois)

1. Ouvrir http://localhost:5678 et créer le compte local n8n.
2. Menu **⋯ → Import from File** : importer `n8n\workflows\full_chain_groq.json`.
3. Ouvrir le nœud **Groq Chat Model**, créer le credential Groq avec la clé, enregistrer.
4. Activer le workflow (interrupteur **Active**).
5. Importer `n8n\workflows\telegram_front.json` (aucun credential : le token est lu dans `.env`) et l'activer.

Un seul workflow `full_chain_*` doit être actif : ils partagent les mêmes webhooks.

## 5. Vérifier que tout fonctionne

```powershell
.\.venv-chain\Scripts\python.exe agent2_diagnosis\tests\preflight.py
.\.venv-chain\Scripts\python.exe agent2_diagnosis\tests\live_check.py
```

`preflight` contrôle les services, les webhooks et le budget Groq ; `live_check` envoie un vrai clip à travers les trois agents et affiche PASS ou FAIL par agent (environ 1 minute, environ 7 000 tokens Groq).

## 6. Utiliser le système

**Par Telegram (scénario de démonstration).**

1. Ouvrir la conversation avec son bot et envoyer `/start`.
2. Envoyer la note vocale `data\audio\demo_clips\demo_3_tentatives_demarrage.ogg` (ou enregistrer 5 à 10 secondes près du moteur, sans parler).
3. Répondre « Renault Clio 2015 diesel », puis « Le démarreur ne tourne pas, j'entends juste un clic ».
4. Après environ une minute : analyse du son, causes probables, puis trois vérifications citées (manuel Clio 4, p. 225).
5. Répondre aux vérifications : diagnostic mis à jour, guide de l'Agent 3 et résumé final.

`/annuler` remet la conversation à zéro.

**Par la page de test** (http://localhost:8501) : le clip de démo est sélectionné par défaut ; « Lancer les 3 agents », répondre aux vérifications, « Envoyer mes réponses ». La page montre l'entrée, la sortie et le JSON brut de chaque agent.

**Par les webhooks** (sans Telegram) :

```powershell
curl.exe -X POST localhost:5678/webhook/agent2-diagnose-audio -F "audio=@data/audio/demo_clips/demo_failed_start.ogg" -F "case=<agent2_diagnosis/tests/cases/clio_starter_pass1.json"
```

## 7. Tests hors ligne (sans clé, sans réseau)

```powershell
.\.venv-agent1\Scripts\python.exe -m pytest -q agent1_audio\tests
.\.venv-chain\Scripts\python.exe -m pytest -q agent2_diagnosis\tests agent3_repair\tests
node n8n\code_nodes\test_agent2_nodes.js
node n8n\telegram_front\test_telegram_front.js
```

Évaluations reproductibles : `evaluation/run_scenarios.py` (scénarios de bout en bout), `agent2_diagnosis/tests/eval_dev_rag.py` (RAG de l'Agent 2, 30 requêtes), `evaluation/run_agent3_retrieval.py` (recherche de l'Agent 3), `prompts/eval/` (avant/après des prompts).

## 8. Dépannage

| Symptôme | Cause et solution |
|---|---|
| `docker info` ne répond pas | le moteur Docker est arrêté : quitter Docker Desktop complètement et le relancer |
| Erreur rouge « Start-Process … introuvable » au lancement | Ollama n'est pas installé : sans gravité, ajouter `-NoOllama` |
| Webhook 404 juste après un import | attendre 15 s, vérifier que le workflow est **Active** et qu'il est le seul `full_chain_*` actif |
| « Error in workflow » sur le nœud Groq | budget Groq épuisé (200 000 tokens par jour et par modèle) : lire l'exécution dans n8n, attendre le lendemain ou utiliser `full_chain_ollama.json` |
| Le bot ne répond pas | vérifier `TELEGRAM_BOT_TOKEN` dans `.env`, puis `docker compose up -d --no-deps n8n` pour que n8n relise le fichier |
| Première analyse audio lente | chargement du modèle : attendre le préchauffage (`/health` de l'Agent 1 renvoie `ready: true`) |
| `storage.sqlite` apparaît modifié dans Git | l'Agent 3 réécrit ce fichier en tournant : ne pas le committer |

Ne jamais redémarrer n8n, le RAG ou un agent pendant une conversation en cours.

## 9. Sécurité et données

- Secrets : `.env` et `agent3_repair\.env` sont exclus de Git ; les workflows exportés ne contiennent aucune clé.
- Les manuels des constructeurs, les clips YouTube annotés et les clips de démonstration ne sont pas redistribués ; seuls les scripts, annotations et index de travail le sont.
- Les codes OBD-II (OBDex) sont sous licence CC0 ; le modèle de détection de parole Silero VAD est sous licence MIT.
