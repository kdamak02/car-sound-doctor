# Telegram front: work in progress (paused 2026-10-17)

Goal: a Telegram bot that talks to the user and calls the full chain (`n8n/workflows/full_chain_*.json`, webhooks `agent2-diagnose-audio` and `agent2-diagnose`).

## Written (all in `n8n/telegram_front/`)
- `telegram_front.js`: the conversation logic as pure functions (voice note -> car -> symptom -> context buttons -> Agent 1 + 2 -> one check at a time with buttons -> pass 2 -> Agent 2 + Agent 3 answer). `test_telegram_front.js` passes (**measured**).
- `build_telegram_front.py` -> `n8n/workflows/telegram_front.json`: polling workflow (getUpdates every 5 s, so no public URL or tunnel), 6 Code nodes, state and a lock file in `/home/node/.n8n/telegram/`. The Code nodes are valid JavaScript (**measured**: `node --check`). **Never run inside n8n yet.**
- `mock_telegram.py`: a fake Telegram Bot API to test everything without a bot or a token.

## Still to do, in this order
1. `docker-compose.yml`, service `n8n`, `environment:` add `- NODE_FUNCTION_ALLOW_BUILTIN=fs` (the nodes use `require('fs')`), then recreate n8n: `docker compose up -d --no-deps n8n`. Check that `$env` is readable in a Code node (n8n 1.80: expected yes). **Not done.**
2. Test with the fake Telegram: root `.env` -> `TELEGRAM_BOT_TOKEN=test` and `TELEGRAM_API_BASE=http://host.docker.internal:8099`; start `python n8n/telegram_front/mock_telegram.py <voice.ogg> 8099`; import and activate `telegram_front.json` and one `full_chain_*.json` (Ollama or Groq credential); push updates with `POST localhost:8099/_push` (a voice message, then texts and `callback_query` presses as in `test_telegram_front.js`); read `GET /_sent`.
3. Likely problems to check: `this.helpers.request` with `formData` inside a Code node (multipart upload of the OGG); the `Reply` node must run once per item; a Schedule Trigger needs the workflow to be Active; the lock file frees itself after 8 minutes if an execution crashes.
4. Real bot: create it with @BotFather, put the token in the root `.env` (git-ignored; never in a chat or a commit), remove `TELEGRAM_API_BASE`, recreate n8n, send a real voice note.
5. Document it in `agent2_diagnosis/HANDOVER.md` section 1 (the row "Telegram trigger ... not built") and section 6.

Known limits of the design: French only; the car is parsed from free text (make, model, year); the context is deduced from the words or asked with 4 buttons; one conversation per chat.
