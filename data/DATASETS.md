# Datasets

Raw data is **not** in git. Download it with the scripts in this folder.
Choice for Agent 1: **real car recordings only**.

## Audio (group A) — `data/audio/raw/`

| Folder | Source | Content | Use | Licence |
|---|---|---|---|---|
| `kaggle_car_diagnostics` | Kaggle `malakragaie/car-diagnostics-dataset` | 1,386 clips; idle (normal, belt, low oil, power steering), braking (normal, worn), startup (normal, dead battery, bad ignition); 437 `combined` clips are artificial mixes | **main training set**, without `combined` | see Kaggle page |
| `hf_engine_phone` | Hugging Face `RashmiKarunarathne/MyDataSet` | 321 phone recordings: misfire 111, healthy 83, tappet 68, belt 59 | closest to Telegram audio | see HF page |
| `vafd` | GitHub `amrrashed/Beyond-Sensors...` (VAFD) | 175 clips of 2.5 s (16 kHz, partly stereo) cut from ~34 recordings: belt, brakes, engine, exhaust, suspension | extra classes, group split mandatory | academic use, cite the paper |
| `cardiag_samples` | GitHub `jlacsam/cardiag` | 23 copies of Kaggle clips | quick tests only, never in training | MIT |

Excluded (`data/audio/excluded/`): AI Mechanic (unclear provenance and labels),
DCASE 2020 ToyCar / fan (toy car in a lab, not a real car).

YouTube clips (`data/audio/youtube/`): collected by one team member; only clips validated by
listening are used; URL list goes to the report annex; clips are never redistributed.

## RAG corpus (groups B and C) — `data/rag/raw/`

| Folder | Source | Content | Use | Licence |
|---|---|---|---|---|
| `obdex` | GitHub `foerbsnavi/OBDex` | 8,907 generic OBD-II codes: causes with likelihood, symptoms | Agent 2 | data CC0 |
| `dtc-database` | GitHub `Wal33D/dtc-database` | 12k+ codes incl. manufacturer codes (SQLite) | Agent 2 lookup | MIT |
| `mechanicdb` | GitHub `MechanicDB/MechanicDB-public` | 90-code sample: ranked fixes, steps, parts, cost | Agent 3 | ODbL |
| `nhtsa` | api.nhtsa.gov | owner complaints, 10 models × 2012-2019 | test queries | US public data |
| `manuals` | official brand websites | owner manuals PDF (to collect by hand) | Agents 2 and 3, page citations | brand terms, not redistributed |

## Scripts

| Script | Downloads | Needs |
|---|---|---|
| `download_rag.py` | obdex, dtc, mechanicdb, nhtsa | nothing |
| `download_external.py` | kaggle, hf (+ dcase, nhtsa: not used anymore for audio) | Kaggle token |

VAFD and cardiag samples come from GitHub (sparse clone of the audio folders).
