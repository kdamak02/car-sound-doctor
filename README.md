# Car Sound Doctor

Advanced Deep Learning final project (ESPRIT, 2026-2027).
A user sends an engine sound and a short description on Telegram. The system detects the likely
fault, asks for physical checks, then returns a cited step-by-step repair guide.

Full design: [ARCHITECTURE.md](ARCHITECTURE.md).

```
Telegram ─► n8n ─► Agent 1 (audio model, FastAPI) ─► Agent 2 (LLM + RAG: checks) ─► user answers
                                                      ─► Agent 3 (LLM + RAG: repair guide) ─► Telegram
```

## Repository map

| Folder | Owner | Content |
|---|---|---|
| `contracts/` | everyone | **JSON schemas exchanged between agents.** Change them only by pull request approved by one person of each group. |
| `agent1_audio/` | Group A | data preparation, models, FastAPI service `/analyze` |
| `rag/` | Group B (shared with C) | PDF ingestion, Qdrant, search service `/search`, retrieval evaluation |
| `agent2_diagnosis/` | Group B | Agent 2 logic and tests |
| `agent3_repair/` | Group C | Agent 3 logic, guardrails, output validation |
| `n8n/` | Group C | exported n8n workflows (the orchestration) |
| `prompts/` | everyone | prompt library P1-P7 (annex of the report) |
| `evaluation/` | everyone | end-to-end test scenarios and results |
| `data/` | everyone | download scripts and dataset list. **Raw data is not in git.** |
| `report/` | everyone | report, slides, demo video link |

## Quick start

Requirements: Git, Python 3.10+, Docker Desktop, ffmpeg.

```bash
git clone <repo-url>
cd car-sound-doctor
cp .env.example .env          # then fill in your own keys (never commit .env)

# data (see data/DATASETS.md)
cd data
python download_rag.py        # RAG corpus (groups B and C)
python download_external.py   # audio datasets (group A, needs a Kaggle token)
cd ..

# start everything: n8n, Qdrant, audio service, RAG service
docker compose up --build
```

| Service | URL |
|---|---|
| n8n | http://localhost:5678 |
| Audio service (Agent 1) | http://localhost:8001/docs |
| RAG search service | http://localhost:8002/docs |
| Qdrant dashboard | http://localhost:6333/dashboard |

Both services start in **mock mode** (`MOCK_MODE=true`): they return valid example answers from
`contracts/examples/`. This lets group C build the whole n8n flow from day one, before the real
models exist. Each group switches its service to real mode when ready.

## How we work

Read [CONTRIBUTING.md](CONTRIBUTING.md). In short:
- never push to `main`; one branch per task (`agent1/prepare-data`, `rag/chunking`...);
- open a pull request, one teammate reviews it;
- any change to `contracts/` needs a reviewer from each group;
- CI checks that the contracts and examples stay valid.

## Team

| Group | Members | Agent |
|---|---|---|
| A | A1 (data), A2 (models), A3 (service + real tests) | Agent 1: audio detection |
| B | B1 (RAG pipeline), B2 (Agent 2 + RAG evaluation) | Agent 2: diagnosis and checks |
| C | C1 (Agent 3 + safety), C2 (n8n + Telegram, project lead) | Agent 3: repair guide |

Task board: shared artifact link (ask C2).

## Deliverables (due 2 days before the validation)

- [ ] exported n8n workflow + this repository with install instructions
- [ ] technical report, 8 pages max (`report/`)
- [ ] prompt library as annex (`prompts/`)
- [ ] PowerPoint presentation
- [ ] demo video, 5 minutes max
