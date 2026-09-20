import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer

from assistant.core import Assistant
from assistant.models import Request
from config import Settings
from memory.sqlite import SQLiteMemory
from tools.registry import ToolRegistry

MAX_BODY = 65536


def make_handler(assistant: Assistant):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, format, *args):
            pass  # Do not log request contents or personal memory.

        def reply(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self.reply(200, {"status": "ok"})
            else:
                self.reply(404, {"error": "Not found"})

        def do_POST(self):
            if self.path != "/requests":
                self.reply(404, {"error": "Not found"})
                return
            # Reject browser-originated requests; this API has no remote authentication.
            if self.headers.get("Origin") is not None:
                self.reply(403, {"error": "Browser origins are not allowed"})
                return
            if self.headers.get_content_type() != "application/json":
                self.reply(415, {"error": "Expected application/json"})
                return
            try:
                if self.headers.get("Transfer-Encoding"):
                    raise ValueError("Transfer-Encoding is not supported")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    self.reply(413, {"error": "Body must contain 1..65536 bytes"})
                    return
                payload = json.loads(self.rfile.read(length))
                request = Request.from_dict(payload)
                if request.action == "dialogue":
                    request = Request(request.action, {"user_id": "local", **request.parameters})
            except (ValueError, UnicodeError, OSError):
                self.reply(400, {"error": "Invalid request body"})
                return
            try:
                response = assistant.handle(request)
                self.reply(200 if response.ok else 400, asdict(response))
            except Exception:
                self.reply(500, {"error": "Internal error"})

    return Handler


def main():
    settings = Settings.from_env()
    with SQLiteMemory(settings.database_path) as memory:
        assistant = Assistant(memory, ToolRegistry(), history_policy=settings.history_policy)
        with HTTPServer(("127.0.0.1", settings.port), make_handler(assistant)) as server:
            print(f"Local API: http://127.0.0.1:{settings.port}")
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()
