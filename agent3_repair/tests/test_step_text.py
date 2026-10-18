import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent3_model import clean_step_text, step_key  # noqa: E402


def test_step_display_text():
    """Seen live (iFixit guide): a title line shown as step 1, wiki quotes around 'off', the same step repeated after 'CAUTION:'."""
    assert clean_step_text("[How to Disconnect a Car or Truck Battery | Step 1: Step 1]") is None
    a = clean_step_text("Before you begin, switch the ignition \'\'\'off\'\'\' and remove the key.")
    assert a == "Before you begin, switch the ignition off and remove the key."
    assert step_key(clean_step_text("CAUTION: Before you begin, switch the ignition \'\'\'off\'\'\' and remove the key.")) == step_key(a)
