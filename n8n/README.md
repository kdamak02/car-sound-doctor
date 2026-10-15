# n8n orchestration (group C, owner C2)

n8n runs in Docker (`docker compose up`), UI at http://localhost:5678.
Inside Docker the services are reached by name: `http://audio-service:8001`, `http://rag-service:8002`.

## Workflows (files in `workflows/`)

| File | Content |
|---|---|
| `main_telegram.json` | Telegram trigger → extraction (P1/P2) → Agent 1 → Agent 2 → wait for answers → Agent 3 → report |
| `ingestion.json` | new PDF in a folder → RAG ingestion |
| `error_handler.json` | Error Trigger: log + apologise to the user |

Node order of the main workflow: see ARCHITECTURE.md section 3.

## Integration plan

1. **Week 1**: Telegram trigger + reply "message reçu" + call to `/analyze` in mock mode.
2. **Week 2**: Agents 2 and 3 as AI Agent nodes, with `/search` (mock) as HTTP tool;
   `code_nodes/validate_output.js` after each.
3. **Week 3**: switch services to `MOCK_MODE=false` one by one; end-to-end tests from `evaluation/`.

## Calling the services

Audio (HTTP Request node): POST `http://audio-service:8001/analyze`, body *Form-Data*,
field `file` = binary from the Telegram node, field `context` = idle|startup|braking|driving.

Search (HTTP Request tool for the agents): POST `http://rag-service:8002/search`, JSON body as in
`contracts/examples/rag_search_request.json`.

## Saving your work

Export after every change: workflow menu → Download → save to `workflows/` → commit.
Check that no API key appears in the exported JSON (credentials are stored by n8n, not in the file).
