"""Agent 2 -> Agent 3 hand-over: turns the case record + Agent 2's last output into Agent 3's input
(agent3_repair/schemas/agent3_input.schema.json). Port of the same rules in n8n/code_nodes/agent2_to_agent3.js (same tests).

to_agent3(case, out) -> dict
  case : case_record (with `diagnosis` = earlier Agent 2 outputs and `check_answers`)
  out  : the last agent2_output (the one that ended the loop)
Status: conclusive -> "confirmed"; no more checks and still not conclusive -> "inconclusive"; otherwise "probable".
"""
import re


def to_agent3(case: dict, out: dict) -> dict:
    ui, v, audio = case.get("user_input") or {}, case.get("vehicle") or {}, case.get("audio") or {}
    top = {t.get("label"): t.get("prob") for t in audio.get("top3") or []}
    answers = {a["check_id"]: a.get("answer") or "Not checked" for a in case.get("check_answers") or []}
    asked = [c for d in (case.get("diagnosis") or []) for c in d.get("checks") or []]
    status = "confirmed" if out.get("conclusive") else ("probable" if out.get("checks") else "inconclusive")
    return {
        "case_id": case["case_id"],
        "language": ui.get("language") if ui.get("language") in ("fr", "en") else "fr",
        "vehicle": {"make": v.get("make") or "", "model": v.get("model") or "", "year": int(v.get("year") or 0),
                    "engine": v.get("fuel") or None},
        "audio": {"class": audio.get("label") or "unknown",
                  "confidence": float(top.get(audio.get("label"), 0.0) if audio.get("status") == "ok" else 0.0)},
        "diagnosis": {"status": status, "causes": [
            {"name": c["name"], "prob": c["prob"],
             "evidence": [e for e in c.get("evidence") or [] if not re.fullmatch(r"(passage )?[\w.-]+_p\d+_c\d+|passage \S+|obdex_\S+", e)]}
            for c in out.get("causes") or []]},
        "checks": [{"question": c["q"], "answer": answers.get(c["id"], "Not checked")} for c in asked],
    }
