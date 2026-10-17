"""Builds n8n/workflows/telegram_front.json (the Telegram front of the chain). Owner: B2.

    python n8n/telegram_front/build_telegram_front.py

Polling (getUpdates every 5 s) instead of a Telegram Trigger: it works on a laptop with no public URL or tunnel.
Needs in the root .env (read by the n8n container): TELEGRAM_BOT_TOKEN (BotFather); optional TELEGRAM_API_BASE (tests with
mock_telegram.py), CHAIN_BASE_URL (default http://localhost:5678/webhook). docker-compose.yml sets NODE_FUNCTION_ALLOW_BUILTIN=fs:
the state of the conversations and a lock file (no two executions at once) live in /home/node/.n8n/telegram/.
It calls the full-chain workflow (n8n/workflows/full_chain_*.json) through its two webhooks, which must be active.
"""
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from agent2_diagnosis.labels import load_labels  # noqa: E402

NS = uuid.UUID("a2a2a2a2-0000-4000-8000-000000000003")
FRONT = (ROOT / "n8n/telegram_front/telegram_front.js").read_text("utf-8")
LABELS_FR = {x["id"]: x["fr"] for x in load_labels(ROOT)["labels"]}

PRELUDE = r"""
const fs = require('fs');
const BASE = $env.TELEGRAM_API_BASE || 'https://api.telegram.org';
const TOKEN = $env.TELEGRAM_BOT_TOKEN;
if (!TOKEN) throw new Error('TELEGRAM_BOT_TOKEN is not set in the root .env (create the bot with @BotFather, then recreate n8n)');
const DIR = '/home/node/.n8n/telegram';
fs.mkdirSync(DIR, { recursive: true });
const LOCK = DIR + '/lock';
const loadState = () => { try { return JSON.parse(fs.readFileSync(DIR + '/state.json', 'utf8')); } catch (e) { return { offset: 0, chats: {} }; } };
const saveState = (st) => fs.writeFileSync(DIR + '/state.json', JSON.stringify(st));
const unlock = () => { try { fs.unlinkSync(LOCK); } catch (e) { /* already free */ } };
"""

SEND = r"""
const tg = (method, body) => this.helpers.httpRequest({ method: 'POST', url: `${BASE}/bot${TOKEN}/${method}`, body, json: true });
const sendAll = async (chatId, messages) => {
  for (const m of messages) {
    const body = { chat_id: chatId, text: String(m.text).slice(0, 4000) };
    if (m.buttons) body.reply_markup = { inline_keyboard: m.buttons.map((row) => row.map((b) => ({ text: b.text, callback_data: b.data }))) };
    try { await tg('sendMessage', body); } catch (e) { /* a failed message must not stop the others */ }
  }
};
"""

POLL = PRELUDE + r"""
// one execution at a time: a lock file (an execution that crashes frees it after 8 minutes)
try { const age = Date.now() - fs.statSync(LOCK).mtimeMs; if (age < 8 * 60 * 1000) return []; unlock(); } catch (e) { /* no lock */ }
try { fs.writeFileSync(LOCK, String(Date.now()), { flag: 'wx' }); } catch (e) { return []; }
try {
  const st = loadState();
  const res = await this.helpers.httpRequest({ method: 'GET', url: `${BASE}/bot${TOKEN}/getUpdates`, qs: { offset: st.offset, timeout: 0, limit: 20 }, json: true });
  const ups = res.result || [];
  if (!ups.length) { unlock(); return []; }
  st.offset = ups[ups.length - 1].update_id + 1;  // the next getUpdates confirms these to Telegram
  saveState(st);
  return ups.map((u) => ({ json: u }));
} catch (e) { unlock(); throw e; }
"""

HANDLE = PRELUDE + FRONT + SEND + r"""
const u = $json;
const m = u.message || (u.callback_query && u.callback_query.message);
if (!m) return { json: { chat_id: null, messages: [], run: null } };
const st = loadState();
const key = String(m.chat.id);
const r = handle(st.chats[key], u, Date.now());
st.chats[key] = r.state;
saveState(st);
if (u.callback_query) { try { await tg('answerCallbackQuery', { callback_query_id: u.callback_query.id }); } catch (e) { /* ignore */ } }
await sendAll(m.chat.id, r.messages);  // the first answer goes out before the long agent run
return { json: { chat_id: m.chat.id, run: r.run, state: r.state } };
"""

RUN = PRELUDE + r"""
const run = $json.run;
if (!run) return { json: { ...$json, reply: null } };
const CHAIN = $env.CHAIN_BASE_URL || 'http://localhost:5678/webhook';
try {
  let reply;
  if (run.kind === 'audio') {
    const f = await this.helpers.httpRequest({ method: 'GET', url: `${BASE}/bot${TOKEN}/getFile`, qs: { file_id: run.file_id }, json: true });
    const audio = await this.helpers.httpRequest({ method: 'GET', url: `${BASE}/file/bot${TOKEN}/${f.result.file_path}`, encoding: 'arraybuffer', json: false });
    reply = await this.helpers.request({ method: 'POST', url: CHAIN + '/agent2-diagnose-audio', json: true, timeout: 300000,
      formData: { audio: { value: Buffer.from(audio), options: { filename: 'note.ogg', contentType: 'audio/ogg' } }, case: JSON.stringify(run.case) } });
  } else {
    reply = await this.helpers.httpRequest({ method: 'POST', url: CHAIN + '/agent2-diagnose', body: run.case, json: true, timeout: 300000 });
  }
  return { json: { ...$json, reply } };
} catch (e) {
  return { json: { ...$json, reply: { error: String((e && e.message) || e) } } };
}
"""

REPLY = PRELUDE + FRONT + SEND + "\nconst LABELS_FR = " + json.dumps(LABELS_FR, ensure_ascii=False) + r""";
if (!$json.run) return { json: { done: true } };  // nothing was run: the answer was already sent
const out = format($json.state, $json.run.kind, $json.reply, LABELS_FR);
const st = loadState();
st.chats[String($json.chat_id)] = out.state;
saveState(st);
await sendAll($json.chat_id, out.messages);
return { json: { done: true, stage: out.state.stage } };
"""

UNLOCK = PRELUDE + r"""
unlock();
return [];
"""


def uid(name):
    return str(uuid.uuid5(NS, name))


def code(name, js, pos, mode="runOnceForEachItem"):
    return {"id": uid(name), "name": name, "type": "n8n-nodes-base.code", "typeVersion": 2, "position": pos,
            "parameters": {"mode": mode, "jsCode": js}}


def link(to):
    return {"main": [[{"node": to, "type": "main", "index": 0}]]}


nodes = [
    {"id": uid("Every 5 seconds"), "name": "Every 5 seconds", "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2, "position": [0, 0],
     "parameters": {"rule": {"interval": [{"field": "seconds", "secondsInterval": 5}]}}},
    code("Poll Telegram", POLL, [260, 0], "runOnceForAllItems"),
    code("Handle update", HANDLE, [520, 0]),
    code("Run agents", RUN, [780, 0]),
    code("Reply", REPLY, [1040, 0]),
    code("Unlock", UNLOCK, [1300, 0], "runOnceForAllItems"),
]
connections = {"Every 5 seconds": link("Poll Telegram"), "Poll Telegram": link("Handle update"), "Handle update": link("Run agents"),
               "Run agents": link("Reply"), "Reply": link("Unlock")}
workflow = {"name": "Car Sound Doctor - Telegram front", "nodes": nodes, "connections": connections, "active": False,
            "settings": {"executionOrder": "v1"}, "tags": [], "pinData": {}}

if __name__ == "__main__":
    out = ROOT / "n8n/workflows/telegram_front.json"
    out.write_text(json.dumps(workflow, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", out.relative_to(ROOT), f"({len(nodes)} nodes)")
