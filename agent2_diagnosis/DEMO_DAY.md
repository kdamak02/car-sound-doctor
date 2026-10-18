# Demo day: checklist and script for the whole chain (Telegram -> Agent 1 -> Agent 2 -> Agent 3)

Replaces the Agent 2 only script in `DEMO.md` for the defense. Facts were measured on one PC; numbers are in `HANDOVER.md` and `tests/eval_results.md`.

## A. One or two days before
- [ ] **Engine recording**: group A prepares it (starter problem, context `startup`); ask them for it. Run it once through `POST /webhook/agent2-diagnose-audio` and check the label, confidence and unusualness. Keep their backup recording and the result.
- [ ] **Real bot**: create it with @BotFather, put the token in the root `.env` (`TELEGRAM_BOT_TOKEN=`; `TELEGRAM_API_BASE` must NOT be set), recreate n8n, activate `telegram_front.json` and one `full_chain_*.json` (steps in `n8n/telegram_front/README.md`). Do a full conversation from your phone.
- [ ] **No recording is made** (decision): plan B is the saved transcript `DEMO_TRANSCRIPT.md` on a slide, the test page in "Chaîne complète" mode (same chain, no phone) and the offline tests run live.
- [ ] **Model choice** (decide now, write it down):
  - Groq `gpt-oss-120b`: best quality, about 35-60 s per pass, but 200,000 tokens per day shared by the team. Run no test batches in the 24 h before.
  - Ollama `qwen2.5:7b`: no quota, 40-70 s per pass with the RAG on the CPU (reranker on the GPU conflicts with Ollama's memory). Workable but weaker answers.
- [ ] **Pull the latest** `integration/agent2-agent3`, run `pytest -q agent2_diagnosis/tests`, `node n8n/code_nodes/test_agent2_nodes.js`, `node n8n/telegram_front/test_telegram_front.js` (all offline, a few seconds).
- [ ] Laptop: charger, Docker Desktop set to start with Windows, Windows updates paused, notifications off, internet checked (Telegram and Groq need it), phone hotspot as plan B.
- [ ] Send the Agent 3 note (`NOTE_FOR_AGENT3.md`) to group C early: their guides are the weakest link.

## B. One hour before (in this order)
1. Start Docker Desktop, wait until `docker info` answers.
2. `.\agent2_diagnosis\start_full_chain.ps1 -Python "<python>" -RagDevice cuda` (Groq) or `-RagDevice cpu` (Ollama). Wait for "up" on all five.
3. `python agent2_diagnosis/tests/preflight.py`: must print "Ready". It checks the services, that both webhooks are active, the Groq budget of the three models, and the settings.
4. **Warm up once**, because the first analysis after a start loads the models (Agent 1 up to a minute, Ollama 20-30 s): send your demo recording through the bot, answer the checks, wait for the guide. Then `/annuler`.
5. Close everything else (browser tabs, games). Put the phone on do-not-disturb. Open: the phone chat, the n8n canvas (execution view), `tests/eval_results.md`, `DEMO_TRANSCRIPT.md`, the test page (full-chain mode).
6. **From now on: never restart n8n, the RAG, Ollama or Agent 1.** A restart during a run makes the run fail.

## C. The demo (about 6 minutes)
| Time | Do | Say (which criterion it answers) |
|---|---|---|
| 0:00 | Slide: the architecture (Telegram -> n8n -> three agents) | "Three agents because three different jobs, data and evaluations: sound (a trained model, not an LLM), diagnosis with the owner's manuals (LLM + RAG), repair guide (LLM + RAG on procedures). Each agent has its own model; they only exchange JSON contracts." (multi-agent) |
| 0:40 | Phone: send the voice note | "Voice and text: the audio goes to our AST model, the text to the LLM." (multimodality) |
| 1:00 | Answer the bot: "Renault Clio 2015 diesel", then "le démarreur ne tourne pas, j'entends un clic" (context is deduced, or press a button) | "Everything the driver says becomes a case record." |
| 1:30 | While it works: show the n8n canvas, the Agent 2 node and its `rag_search` tool calls | "A real AI Agent node. The model decides what to search; the tool is our RAG service with hybrid search and a reranker. The answer can only cite passages the tool returned." (automation platform, RAG) |
| 2:30 | Read the bot's first answer: sound line, causes with %, advice with a page, the first check | "The advice is ordered: most likely cause first, with the manual page. The sound analysis is a hint, never a verdict." |
| 3:00 | Answer the checks with the buttons | "Pass 2 re-ranks with the answers: a contradicted cause is capped by code, not by the model." |
| 4:00 | Read the final message: Agent 2 conclusion + Agent 3 guide with sources | "Agent 3 has its own corpus and a validator; if sources do not support a fix it must say 'see a professional'." |
| 4:40 | Show `tests/eval_results.md` | "Retrieval evaluation on 30 queries: chunking by heading beats fixed windows, hybrid beats dense, the reranker rejects 4 of 4 out-of-corpus questions (1 of 4 without). One labeller, so indicative." (RAG evaluation) |
| 5:20 | Show `prompts/` (P1-P5 with versions and before/after) | "The prompt library: versions, the failure that caused each change, and what we measured." (prompt engineering) |

If the manuals do not cover a noise, the bot says it will not invent an answer: that is deliberate (show it only if asked; do not demo a belt noise, the manuals do not cover it).

## D. If something goes wrong
| Symptom | Do |
|---|---|
| The bot does not answer | Say "the polling bot checks every 5 s"; wait 10 s. Still nothing: `preflight.py`; is the front workflow active? |
| "service n'a pas pu répondre" | Almost always the Groq quota or a model down. Switch to the Ollama full-chain workflow (deactivate one, activate the other; 1 minute) or show `DEMO_TRANSCRIPT.md` and the test page. |
| More than 3 minutes of silence | Say "it is on CPU, this is the slow part", wait one more minute, then switch to the test page or show `DEMO_TRANSCRIPT.md`. |
| Agent 3 gives a generic guide | Be honest: "its corpus is still generic for this fault; the validator accepted it because the sources are cited; improving the corpus is the next step." Do not claim more. |
| Docker or n8n crashed | `DEMO_TRANSCRIPT.md` + show `pytest` and the Node tests running live (offline, seconds) as proof the logic is tested. |
| A jury question you cannot answer | "That is not measured; here is what we measured instead." Never invent a number. |

## E. Likely questions (short, honest answers)
- *Why not one agent?* Different data, different models, different evaluation; contracts make them replaceable. The cost: more moving parts (we paid it in integration time).
- *Why a reranker?* Dense scores do not separate "no answer" from "weak answer". Measured: 1 of 4 out-of-corpus queries rejected without it, 4 of 4 with it, at a cost of some hit rate on the answerable ones (0.923 without, 0.846 with; see `tests/eval_results.md`).
- *What guarantees the answer is grounded?* Code, not the model: only retrieved passages can be cited; uncited causes and checks are dropped; a second search checks that a cited passage really comes back for the cause; Agent 3 has its own validator. Limit: an unrelated code table can still pass the second check.
- *Why a small local model and Groq?* Free tiers: Gemini gives about 5 runs a day, Groq 200,000 tokens a day per model. The workflow is the same for Ollama, Groq and Gemini.
- *Accuracy of the audio model?* Agent 1's own figures: macro-F1 0.72 on Telegram-like audio with the context, 12 classes, labels from open datasets (their `agent1_audio/INTEGRATION.md`). Not our number.
- *What does not work yet?* Generic repair guides, one real-bot test on one phone only, a small evaluation set, the 30-minute timeout when the driver never answers, no multi-user test.
- *Privacy?* Voice notes are processed locally by Agent 1; the text goes to the chosen LLM provider (Groq/Gemini) or stays local with Ollama.

## F. After the demo
`/annuler` in the bot, keep the logs, note what failed for the report.
