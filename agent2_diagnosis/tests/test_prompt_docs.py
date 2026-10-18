"""The prompt library files must contain the exact prompt text that the Telegram front runs (no drift between documentation and code)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "prompts/eval"))
from p1_prompts import PROMPTS as P1  # noqa: E402
from p2_p7_prompts import PROMPTS as P27  # noqa: E402


def doc(name):
    return (ROOT / "prompts" / name).read_text("utf-8").replace("\r\n", "\n")


def test_library_files_hold_the_prompts_in_use():
    assert P1["v4"].rstrip() in doc("P1_extraction.md")
    assert P27["p2"]["v3"].rstrip() in doc("P2_clarification.md")
    assert P27["p7"]["v4"].rstrip() in doc("P7_final_message.md")


def test_the_workflow_runs_the_documented_versions():
    wf = (ROOT / "n8n/workflows/telegram_front.json").read_text("utf-8")
    import json
    code = "\n".join(n["parameters"].get("jsCode", "") for n in json.loads(wf)["nodes"])
    for text in (P1["v4"], P27["p2"]["v3"], P27["p7"]["v4"]):
        assert json.dumps(text, ensure_ascii=False)[1:-1] in code.replace("\\\\", "\\") or json.dumps(text, ensure_ascii=False) in code


def test_every_library_prompt_documents_versions_and_failures():
    for name in ("P1_extraction.md", "P2_clarification.md", "P3_audio_description.md", "P4_checks_generation.md", "P5_reranking.md", "P7_final_message.md"):
        text = doc(name)
        assert "## Versions" in text and "Failure cases and before / after" in text, name
