# RAG (shared by Agent 2 and Agent 3) — owner B1, evaluation B2

```
data/rag/raw/  ──ingest──►  chunks + metadata  ──embed──►  Qdrant  ◄──/search──  n8n (Agents 2, 3)
```

## Folders

| Path | Content |
|---|---|
| `ingest/` | parse PDFs (pdfplumber, tables as Markdown), convert OBD/MechanicDB rows to text, chunk (A: recursive 500/50, B: by structure), embed, upsert into Qdrant |
| `service/` | FastAPI `/search` (mock until the index exists) |
| `eval/` | labelled queries and the retrieval evaluation (Recall@5, MRR, hit rate, no-answer) |

## Payload stored with every chunk in Qdrant

The filters of `/search` rely on these fields. **Same names everywhere.**

| Field | Example | Notes |
|---|---|---|
| `chunk_id` | `peugeot208_fr_p142_c3` | unique, stable across re-ingestion |
| `text` | | the chunk, heading prefixed for strategy B |
| `source` | `peugeot_208_2019-2023_fr.pdf`, `obdex` | file name or dataset |
| `page` | `142` or `null` | null only for structured sources |
| `make`, `model` | `Peugeot`, `208` | `null` for generic sources |
| `year_from`, `year_to` | `2019`, `2023` | |
| `system` | `belt` | engine, brakes, belt, ignition, electrical, exhaust, steering, suspension, transmission, fuel, other |
| `section` | `symptoms` | symptoms, causes, procedure, safety, maintenance, codes |
| `language` | `fr` | fr, en |
| `chunking` | `A` or `B` | to compare both strategies in one collection |

Agent 2 searches `section in [symptoms, causes, codes]`, Agent 3 `section in [procedure, safety, maintenance]`.

## Run the service

```bash
pip install -r rag/requirements.txt
uvicorn rag.service.app:app --port 8002 --reload     # from the repo root
```
