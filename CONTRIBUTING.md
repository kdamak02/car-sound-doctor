# How we work

## Branches

- `main` is always working. Nobody pushes to it directly.
- One branch per task, prefixed by area:
  `agent1/...`, `rag/...`, `agent2/...`, `agent3/...`, `n8n/...`, `prompts/...`, `report/...`
  Example: `agent1/prepare-data`, `rag/chunking-by-structure`.

```bash
git checkout main && git pull
git checkout -b agent1/prepare-data
# ... work ...
git add . && git commit -m "agent1: convert all audio to 16 kHz mono"
git push -u origin agent1/prepare-data
```
Then open a Pull Request on GitHub.

## Pull requests

- Small and focused: one task per PR.
- One reviewer minimum, ideally from **another group** (everyone must understand the whole system for the oral exam).
- **Changes to `contracts/`**: one reviewer from each group (A, B and C), because the other agents depend on them.
- CI must be green.

## Commit messages

`<area>: <what changed>`, for example `rag: add bge reranker`, `n8n: add wait node for user answers`.

## What never goes into git

- `.env` and any key or token (Kaggle, LLM API, Telegram bot)
- raw data (`data/audio/raw`, `data/rag/raw`), model weights, Qdrant storage
- YouTube clips (copyright): keep only the list of URLs and timestamps

If you commit a secret by mistake: tell the team, revoke the key immediately, then remove it.

## Contracts first

Agents talk only through the JSON formats in `contracts/`. If you need a new field:
1. propose it in a PR that updates the schema **and** the example;
2. update the mock in your service;
3. tell the groups that consume it.

## n8n workflows

n8n stores workflows in its own database, not in files. After a change:
n8n menu → Download (export JSON) → save in `n8n/workflows/` with the same file name → commit.
Before exporting, check that no credential value appears in the JSON.

## Prompts

Every prompt lives in `prompts/` with its version history. Never edit a prompt only inside n8n:
update the file, then copy it into n8n. This history is the report annex.
