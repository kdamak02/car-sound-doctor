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

## Install and run

**Follow [INSTALL.md](INSTALL.md)** (Windows, PowerShell): one setup script (`scripts/setup_demo_pc.ps1`), one start script
(`agent2_diagnosis/start_full_chain.ps1`), then import `n8n/workflows/full_chain_groq.json` and `n8n/workflows/telegram_front.json` in n8n.

| Service | URL |
|---|---|
| n8n | http://localhost:5678 |
| Agent 1, audio (FastAPI) | http://localhost:8001/docs |
| RAG search of Agent 2 | http://localhost:8002/docs |
| Agent 3, repair guide (FastAPI) | http://localhost:8003/docs |
| Test page of the three agents | http://localhost:8501 |

Check: `python agent2_diagnosis/tests/preflight.py`, then `python agent2_diagnosis/tests/live_check.py` (PASS/FAIL per agent).

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

- [x] exported n8n workflows (`n8n/workflows/`) + this repository with install instructions (`INSTALL.md`)
- [x] technical report, 8 pages max, with the prompt library as annex A (`prompts/PROMPT_LIBRARY.md` is the source)
- [x] PowerPoint presentation
- [x] demo video, 5 minutes max (intro and closing clips in `docs/video/`, logo in `docs/logo/`)
