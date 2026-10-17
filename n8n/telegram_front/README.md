# Telegram front

A bot that talks to the driver and calls the full chain (`n8n/workflows/full_chain_*.json`: Agent 1, Agent 2, Agent 3).
Conversation: voice note -> car (free text: make, model, year, fuel) -> symptom (one sentence) -> when the noise appears (4 buttons,
skipped when the sentence says it) -> Agent 1 + Agent 2 -> checks one at a time (buttons or text) -> pass 2 -> Agent 2 answer + Agent 3 guide.
Commands: /start, /annuler. French only. Unusable recording -> asks for a new one. Agent 1 down or slow -> text only. No answer in the manuals -> "consultez un mécanicien".

## Files
- `telegram_front.js`: the conversation logic (pure functions). `test_telegram_front.js`: node test, in CI.
- `build_telegram_front.py` -> `n8n/workflows/telegram_front.json` (6 Code nodes, polls getUpdates every 5 s: no public URL or tunnel needed).
- `mock_telegram.py`: a fake Telegram Bot API to test without a bot.

## Set up the real bot
1. Telegram: talk to @BotFather, `/newbot`, copy the token. **Do not paste it in a chat or a commit.**
2. Root `.env` (git-ignored): `TELEGRAM_BOT_TOKEN=<token>` (leave `TELEGRAM_API_BASE` unset). `docker-compose.yml` already sets `NODE_FUNCTION_ALLOW_BUILTIN=fs`.
3. Recreate n8n so it reads the file: `docker compose up -d --no-deps n8n`.
4. Import and ACTIVATE one `n8n/workflows/full_chain_*.json` (with its model credential) and `n8n/workflows/telegram_front.json` (no credential: the token comes from `.env`).
5. Send a voice note to the bot. The state and a lock file are in `/home/node/.n8n/telegram/` (delete that folder to reset every conversation).

## Test without Telegram
`python n8n/telegram_front/mock_telegram.py voice.ogg 8099`; root `.env`: `TELEGRAM_BOT_TOKEN=test` and `TELEGRAM_API_BASE=http://host.docker.internal:8099`; recreate n8n; activate both workflows;
push updates with `POST localhost:8099/_push` (a `message` with `voice`, texts, `callback_query` presses) and read `GET localhost:8099/_sent`. Restore `.env` afterwards.

## Measured (fake Telegram, local Ollama, synthetic clip, real agents)
A full conversation worked: voice -> car -> symptom -> Agent 1 analysis line -> causes with advice -> 3 checks by buttons -> pass 2 -> Agent 3 guide with sources.
Takes about 1.5 to 3 minutes in total. **Not tested**: a real bot on Telegram's servers, several chats at once, a real engine recording.
Known: the first Agent 1 analysis after a restart is slow (model load; the call timeout is 90 s). Agent 3's guide is generic (see HANDOVER.md).
