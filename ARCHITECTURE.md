# Car Sound Doctor: architecture

Advanced Deep Learning final project (AI Automation). A user sends an engine sound plus a short description on Telegram. The system detects the likely fault, asks for physical checks, then returns a cited step-by-step repair guide.

## 1. Flow

```
Telegram: audio + text
   │
   ▼
[n8n Trigger] ─► download OGG ─► ffmpeg → 16 kHz wav ─► (voice context? Whisper → text)
   │
   ▼
AGENT 1  Audio Detection   (FastAPI: log-mel → anomaly score + fault class + confidence)
   │  case.audio = {class, top3, anomaly_score, confidence}
   ▼
AGENT 2  Diagnosis & Checks (LLM + RAG on troubleshooting tables)
   │  asks 3-5 physical checks, each with a manual citation
   ▼
Telegram question ─► [n8n Wait node: user reply, timeout 30 min]
   │  case.checks = user answers
   ▼
AGENT 2 (second pass): re-rank causes with the answers
   │  ├─ inconclusive & round < 2 ─► ask follow-up checks (loop)
   │  └─ else continue
   ▼
AGENT 3  Repair Guide (LLM + RAG on repair procedures, guardrails)
   │  steps with citations, safety warnings, tools, difficulty
   ▼
Telegram: report (text, optional Piper TTS summary)
```

All agents read and write one JSON **case record**, stored in n8n (workflow data / Postgres or Google Sheet) keyed by the Telegram chat id.

## 2. Agents

| | Agent 1: Audio Detection | Agent 2: Diagnosis & Checks | Agent 3: Repair Guide |
|---|---|---|---|
| Job | Sound to fault hypothesis | Fault hypothesis to verification checks, then re-ranking | Final cited repair guide |
| Type | DL model service, not an LLM | LLM + RAG, two passes | LLM + RAG, guardrailed |
| Tools | FastAPI `/analyze` (librosa, PyTorch) | Qdrant search (filter: section=symptoms/causes) | Qdrant search (filter: section=procedure/safety); optional web search as labeled fallback |
| Model | AST (AudioSet) embeddings + light classifier; autoencoder on log-mel for anomaly | Sonnet-class | Sonnet-class |
| Input | wav | audio result + user text (car make/model/year, symptom) | re-ranked causes + user check answers + passages |
| Output | `{class, top3, anomaly_score, confidence}` | `{causes: [{name, prob, evidence}], checks: [{q, why, source, page}]}` | `{steps: [{n, action, source, page, warning}], tools, difficulty, confidence}` |

User text extraction (car, symptom, missing info) is a small Haiku-class LLM call in n8n before Agent 1, not a separate agent.

## 3. n8n layout

1. **Telegram Trigger** (voice/audio/text)
2. **Switch**: new case vs reply to an open case
3. **HTTP Request**: download file, convert via ffmpeg service
4. **Whisper** (only if the context is a voice note)
5. **Extraction LLM** (JSON: make, model, year, symptom, missing_info); **IF** missing_info → ask, **Wait**
6. **HTTP Request** to the FastAPI audio service (timeout 20 s, retry 2, fallback: skip audio and tell the user)
7. **Agent 2 pass 1** (LLM + Qdrant tool) → **Telegram** checks question → **Wait** (timeout 30 min)
8. **Agent 2 pass 2** (re-rank) → **IF** inconclusive and round < 2 → back to 7
9. **Agent 3** (LLM + Qdrant tool) → **Code** node: validate JSON and citations
10. **Telegram** send report (+ Piper TTS voice summary)
11. **Error Trigger** workflow: log, message the user
12. Separate **Ingestion workflow**: new PDF in a folder → extract → clean → chunk → embed → Qdrant

## 4. RAG

| Item | Choice |
|---|---|
| Corpus | 20-40 public documents: workshop and owner manuals, troubleshooting tables (symptom, cause, remedy), OBD/DTC guides. FR + EN. A few car makes, so neighbouring manuals act as distractors. |
| Parsing | pdfplumber; tables kept as Markdown; Tesseract only for scans |
| Metadata | make, model, year range, system (engine, brakes, belt...), section (symptoms / causes / procedure / safety), language, page, source |
| Chunking | A: recursive 500 tokens / overlap 50. B: by structure (heading or table row, max 600, heading prefixed). Compare both. |
| Embeddings | BGE-M3 (multilingual); compare multilingual-e5 |
| Vector store | Qdrant (cosine, HNSW, payload filters) |
| Retrieval | metadata filter → dense (or hybrid dense+BM25 with RRF) → top 20 → bge-reranker → top 5 |
| Fallback | exact model missing → relax to make, then generic system; tell the user |
| Evaluation | 25-30 hand-labelled queries (direct, reworded, ambiguous, no-answer, unknown car). Recall@5, MRR, hit rate, % no-answer correctly rejected. Matrix: chunking A/B × dense/hybrid × rerank on/off. |

## 5. Audio model

- **Data:** no large public real-car-fault set exists. Plan: Hugging Face phone-recorded engine set and OtoMobile (request access) for fault classes; MIMII/DCASE for pretraining the anomaly model; the synthetic procedural engine set and noise/Opus-codec augmentation to mimic Telegram audio.
- **Classes:** coarse (normal, knock, belt squeal, grinding, misfire/rough idle, exhaust leak), not specific parts. The specific fault is a hypothesis that Agent 2's checks confirm.
- **Models compared:** MFCC + Isolation Forest (baseline), conv autoencoder (anomaly), AST embeddings + light classifier. Metrics: AUC, F1, confusion matrix.
- The LLMs never receive raw audio; Agent 1 returns a structured text description (class, confidence, dominant frequency, periodicity).
- State the limits in the report: small data, phone noise, domain shift.

## 6. Prompt library (separate versioned file)

| ID | Prompt | Technique | Failure to watch |
|---|---|---|---|
| P1 | Extraction | few-shot + strict JSON | invented car model, mixed FR/darija |
| P2 | Clarification | role + rules | too many questions |
| P3 | Audio description | template | LLM over-interprets the score |
| P4 | Checks generation | role + CoT + citation required | generic checks not in the manual |
| P5 | Re-ranking | structured reasoning over answers | ignores contradictory answers |
| P6 | Repair guide | guardrails + JSON | steps without citation, unsafe advice |
| P7 | Final message / TTS | length and style limits | too long for voice |

Each entry: role, technique, observed failures, fix, before/after.

## 7. Guardrails and error handling

- No step or check is accepted without a manual citation; otherwise the answer is "insufficient information".
- Safety rules: never advise driving with brake, steering or overheating faults; warn before any risky action; always recommend a mechanic if confidence is low.
- Web search results are labelled "unverified".
- Loop cap: 2 rounds, then "uncertain, see a mechanic".
- Timeouts and retries per node; if the audio service is down, continue with text only and say so.
- Cost control: Haiku-class for extraction, Sonnet-class for Agents 2 and 3, cache repeated retrievals.

## 8. Why this architecture

| Alternative | Why it is worse here |
|---|---|
| One big prompt | Cannot use an audio model; hallucinates faults; no way to use the user's physical checks |
| Router only | No sequential reasoning; user feedback not integrated |
| Parallel debate of several investigators | Costs 3× the API for a case where user checks give the missing evidence cheaper; harder to defend |
| Deep hierarchy | Too complex and costly for 5 weeks |

A sequential chain with one human-in-the-loop step fits the task: the order of reasoning (listen, suspect, check, confirm, fix) is fixed, and each stage needs different tools and a different model.

## 9. Risks

| Risk | Response |
|---|---|
| Little real fault audio | coarse classes, augmentation, state the limit |
| Weak retrieval on tables | structure chunking, metadata filters, reranker, documented comparison |
| User never answers | Wait timeout, partial report |
| Hallucinated procedures | mandatory citations, no-answer path |
| Demo latency | pre-tested scenario, Haiku for light steps, parallel download/convert |

## 10. Mapping to the grading

| Criterion | Where |
|---|---|
| Prompt engineering 20% | §6 library with iterations |
| RAG 25% | §4 corpus, two chunkings, evaluation |
| Multi-agent 25% | §2, §8 justification |
| Multimodality 15% | audio in (§5), TTS out |
| Demo/report/Q&A 15% | live demo with a pre-tested Telegram scenario |
