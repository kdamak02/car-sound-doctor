# Agent 1 — audio detection (group A)

Sound → fault hypothesis. Not an LLM: a deep learning model behind a FastAPI service.
Output contract: `contracts/agent1_output.schema.json`. Classes: `contracts/labels.json`.

| Path | Owner | Content |
|---|---|---|
| `src/prepare_data.py` | A1 | raw datasets → 16 kHz mono WAV in `data/audio/processed/`, unified labels, `metadata.csv` with `source_id` for the group split |
| `src/audio_utils.py` | all | decoding (incl. Telegram OGG/Opus) and the plain-fact description |
| `src/model.py` | A2 | `predict()` used by the service in real mode |
| `notebooks/` | A2 | experiments: MFCC + Isolation Forest, conv autoencoder, AST + classifier |
| `service/` | A3 | FastAPI `/analyze`, Dockerfile |
| `tests/` | A3 | service tests (contract compliance) |

## Data (only real car recordings)

| Dataset | Folder in `data/audio/raw/` | Kept |
|---|---|---|
| Kaggle Car Diagnostics | `kaggle_car_diagnostics` | yes, **without** the `combined` folders (artificial mixes) |
| Hugging Face phone recordings | `hf_engine_phone` | yes |
| VAFD | `vafd` | yes; clips are 2.5 s at 32 kHz, `_segment_N` = same recording |
| cardiag samples | `cardiag_samples` | quick tests only (copies of Kaggle clips) |
| AI Mechanic, DCASE ToyCar | `data/audio/excluded/` | no (unclear labels / not a real car) |
| YouTube clips | `data/audio/youtube/` | after human validation only |

**Split train / val / test by source recording** (`source_id`), never at random:
segments of one recording must stay on the same side.

## Run the service

```bash
cd agent1_audio
pip install -r requirements.txt
uvicorn service.app:app --port 8001 --reload        # http://localhost:8001/docs
pytest -q tests
```
