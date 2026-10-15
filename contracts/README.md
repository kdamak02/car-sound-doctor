# Contracts between agents

The agents never call each other's code. They exchange JSON in these formats, through n8n.
If every team respects them, integration in week 3 is just wiring.

| File | Produced by | Consumed by |
|---|---|---|
| `labels.json` | Group A | Agent 1 model, Agent 2 prompts |
| `agent1_output.schema.json` | Audio service `POST /analyze` | n8n, Agent 2 (prompt P3/P4) |
| `rag_search.schema.json` | RAG service `POST /search` | Agents 2 and 3 |
| `citation.schema.json` | Agents 2 and 3 | guardrail validator, final message |
| `agent2_output.schema.json` | Agent 2 (LLM in n8n) | n8n (Telegram questions), Agent 3 |
| `agent3_output.schema.json` | Agent 3 (LLM in n8n) | guardrail validator, Telegram, TTS |
| `case_record.schema.json` | n8n | everyone: the full state of one conversation |

`examples/` holds one valid example of each. The services return them in mock mode.

## Check

```bash
pip install jsonschema
python contracts/validate.py                              # all examples
python contracts/validate.py agent3_output my_output.json # your own output
```

## Changing a contract

Pull request that updates the schema **and** the example, reviewed by one person of each group.
Then update your mock and tell the consumers.
