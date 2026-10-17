"""A tiny fake of the Telegram Bot API, to test the Telegram front without a bot, a token or the internet.

    python n8n/telegram_front/mock_telegram.py voice_note.ogg [port]        # default port 8099

Set TELEGRAM_API_BASE=http://host.docker.internal:8099 (and any TELEGRAM_BOT_TOKEN) in the root .env, recreate n8n, activate the workflow.
Drive it:  POST /_push {"message": {...}}  adds an update;  GET /_sent  lists what the bot sent;  POST /_reset  clears everything.
Implements getUpdates (offset confirms), getFile, the file download, sendMessage and answerCallbackQuery. Nothing else.
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

AUDIO = open(sys.argv[1], "rb").read() if len(sys.argv) > 1 else b"OggS"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8099
LOCK = threading.Lock()
UPDATES, SENT, NEXT = [], [], [1]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def reply(self, obj, status=200, raw=None):
        body = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream" if raw is not None else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def route(self, method):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = u.path
        with LOCK:
            if p == "/_push":
                upd = self.body()
                upd["update_id"] = NEXT[0]
                NEXT[0] += 1
                UPDATES.append(upd)
                return self.reply({"ok": True, "update_id": upd["update_id"]})
            if p == "/_sent":
                return self.reply(SENT)
            if p == "/_reset":
                UPDATES.clear(); SENT.clear()
                return self.reply({"ok": True})
            if p.endswith("/getUpdates"):
                data = self.body() if method == "POST" else {}
                off = int(q.get("offset") or data.get("offset") or 0)
                if off:
                    UPDATES[:] = [x for x in UPDATES if x["update_id"] >= off]  # Telegram forgets confirmed updates
                return self.reply({"ok": True, "result": UPDATES[: int(q.get("limit") or 100)]})
            if p.endswith("/getFile"):
                return self.reply({"ok": True, "result": {"file_id": q.get("file_id"), "file_path": "voice/note.ogg"}})
            if p.startswith("/file/bot"):
                return self.reply(None, raw=AUDIO)
            if p.endswith("/sendMessage"):
                SENT.append(self.body())
                return self.reply({"ok": True, "result": {"message_id": len(SENT)}})
            if p.endswith("/answerCallbackQuery"):
                return self.reply({"ok": True, "result": True})
        self.reply({"ok": False, "description": "not found " + p}, 404)

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")


if __name__ == "__main__":
    print(f"mock Telegram on :{PORT}, audio {len(AUDIO)} bytes")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
