"""Real model entry point, used when MOCK_MODE=false. Owner: A2.

Contract: return a dict valid against contracts/agent1_output.schema.json
(`description` is merged with audio_utils.describe() by the service).
Labels must come from contracts/labels.json.
"""


def predict(x, context="unknown"):
    """x: mono float32 waveform at 16 kHz."""
    raise NotImplementedError(
        "Model not plugged in yet: keep MOCK_MODE=true until A2 exports a model to models/.")
