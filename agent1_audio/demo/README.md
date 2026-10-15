# Agent 1 test bench (Streamlit)

```bash
pip install streamlit requests pandas jsonschema
streamlit run agent1_audio/demo/app.py      # from the repo root
```

| Mode (sidebar) | Needs | Use |
|---|---|---|
| Modèle direct | `models/agent1_ast.joblib`, `models/agent1_ae.pt`, `pip install -r agent1_audio/requirements-train.txt` | quick tests on Lightning or a strong PC |
| Service HTTP /analyze | the audio service running (`docker compose up audio-service` or `uvicorn service.app:app --port 8001` from `agent1_audio/`) | checks the exact call n8n will make |

Inputs: upload a file (a Telegram voice note `.ogg` works), record with the microphone, or pick a
clip from the test split (shows the true label). "Simuler une note vocale Telegram" re-encodes
the sound with Opus before the analysis.

Outputs: spectrogram, predicted fault + top 3, confidence, anomaly score, measured facts, the
text sent to Agent 2 (prompt P3) and the JSON contract check.
