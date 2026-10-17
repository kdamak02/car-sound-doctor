"""Demo-day preflight: is everything up, active and within budget?   python agent2_diagnosis/tests/preflight.py
Checks the five services, the n8n webhooks (registered = workflow active), the Groq budget (one small request per model, about 8k tokens
in total), the Ollama model, and the settings in the .env files (never printing a key). Exit code 1 if something blocking is wrong."""
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
bad, warn = [], []


def show(ok, label, detail="", blocking=True):
    print(("  OK   " if ok else ("  FAIL " if blocking else "  WARN ")) + label + (f"  {detail}" if detail else ""))
    if not ok:
        (bad if blocking else warn).append(label)


def get(url, **kw):
    try:
        return requests.get(url, timeout=kw.pop("timeout", 5), **kw)
    except requests.RequestException:
        return None


def env(path):
    out = {}
    p = ROOT / path
    for line in (p.read_text("utf-8").splitlines() if p.exists() else []):
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("\"'")
    return out


print("Services")
r = get("http://localhost:5678/healthz")
show(bool(r and r.ok), "n8n :5678")
r = get("http://localhost:8002/health")
j = r.json() if r and r.ok else {}
show(bool(j), "RAG :8002", f"reranker={j.get('rerank')} chunks={j.get('chunks')}" if j else "")
show(bool(j.get("rerank")) if j else False, "RAG reranker on", blocking=False)
r = get("http://localhost:8001/health")
j = r.json() if r and r.ok else {}
show(bool(j) and j.get("mock") is False, "Agent 1 :8001 (real mode)", f"mock={j.get('mock')}" if j else "")
r = get("http://127.0.0.1:8003/health")
show(bool(r and r.ok), "Agent 3 :8003")
r = get("http://localhost:11434/api/tags")
models = [m["name"] for m in r.json().get("models", [])] if r and r.ok else []
show("qwen2.5:7b" in models, "Ollama + qwen2.5:7b (fallback LLM)", blocking=False)
show(bool(get("http://localhost:8501")), "Test page :8501", blocking=False)

print("n8n workflows (a webhook answers 'not registered for GET' only when its workflow is active)")
for path in ("agent2-diagnose", "agent2-diagnose-audio"):
    r = get(f"http://localhost:5678/webhook/{path}")
    ok = r is not None and "not registered for GET" in r.text
    show(ok, f"/webhook/{path}", "" if ok else "workflow not active (or two full-chain workflows share the path)")

print("Settings")
e = env(".env")
show(bool(e.get("TELEGRAM_BOT_TOKEN")) and e.get("TELEGRAM_BOT_TOKEN") != "test-token", "TELEGRAM_BOT_TOKEN set in the root .env", blocking=False)
show(not e.get("TELEGRAM_API_BASE"), "TELEGRAM_API_BASE not set (it points to the fake Telegram)", blocking=False)
key = env("agent3_repair/.env").get("GROQ_API_KEY", "")
show(len(key) > 20, "GROQ_API_KEY in agent3_repair/.env (Agent 3 needs it)")

print("Groq budget (tokens per day are shared by the whole team; one Agent 2 run needs about 7,000)")
if len(key) > 20:
    for m in ("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"):
        try:
            x = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": "Bearer " + key},
                              json={"model": m, "messages": [{"role": "user", "content": "x" * 28000 + " hi"}], "max_tokens": 5}, timeout=40)
            msg = "" if x.ok else x.json().get("error", {}).get("message", "")[-70:]
            show(x.ok, m, "budget for at least one run" if x.ok else "LIMITED: " + msg, blocking=(m == "qwen/qwen3.8-27b"))
        except requests.RequestException as ex:
            show(False, m, str(ex)[:60], blocking=False)

print()
if bad:
    print("BLOCKING:", "; ".join(bad))
    sys.exit(1)
print("Ready." + (f" Warnings: {'; '.join(warn)}" if warn else ""))
