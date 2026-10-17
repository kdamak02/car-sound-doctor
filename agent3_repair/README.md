# Agent 3 — Repair Guide

Agent 3 is integrated here as a FastAPI service with the bundled v4 model, hybrid local RAG corpus, guardrail validation, and a Streamlit testbench. All paths used by the model resolve within this folder.

## Setup

From the repository root, install dependencies and configure Groq:

```powershell
python -m pip install -r agent3_repair/requirements.txt
Copy-Item agent3_repair/.env.example agent3_repair/.env
# Set GROQ_API_KEY in agent3_repair/.env
```

## Run the FastAPI service

```powershell
uvicorn api:app --app-dir agent3_repair --host 127.0.0.1 --port 8003
```

- `GET /health` — service status
- `POST /search` — retrieve relevant manual passages for a case record
- `POST /generate` — create and validate a repair guide

`/generate` accepts `case_record` plus optional `retrieved_sources`. Set `retrieve: true` (or omit `retrieved_sources`) to use the bundled hybrid RAG index. The Groq API key is read from `agent3_repair/.env` or the process environment.

## Run the Streamlit testbench

```powershell
streamlit run agent3_repair/app.py
```

The testbench supports preset scenarios and custom case/source JSON. Choose **Live RAG** to search the bundled corpus. The first run builds the local index from `data/rag_chunks.json`.

The original repository guardrails remain in `guardrails/`; the integrated v4 contract validation and source grounding checks are in `validation/`.
