import json
import socket
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer

from assistant.core import Assistant
from assistant.models import Request
from config import Settings
from memory.sqlite import SQLiteMemory
from tools.registry import ToolRegistry
from security.store import AuthenticationError

MAX_BODY = 65536


def make_handler(assistant: Assistant):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def finish(self):
            # Early denials can leave body bytes unread. Send FIN before the final
            # close, then drain a bounded amount to avoid resetting the response on Windows.
            try:
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_WR)
                deadline = time.monotonic() + 0.1
                remaining = MAX_BODY
                while remaining > 0 and time.monotonic() < deadline:
                    self.connection.settimeout(max(0.001, deadline - time.monotonic()))
                    chunk = self.connection.recv(min(8192, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            except OSError:
                pass
            finally:
                super().finish()

        def log_message(self, format, *args):
            pass  # Do not log request contents or personal memory.

        def reply(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if status == 401:
                self.send_header("WWW-Authenticate", "Bearer")
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
            try:
                headers = self.headers.get_all("Authorization", [])
                if len(headers) != 1:
                    raise AuthenticationError()
                parts = headers[0].split()
                if len(parts) != 2 or parts[0].casefold() != "bearer" or assistant.security is None:
                    raise AuthenticationError()
                user = assistant.security.authenticate(parts[1])
            except AuthenticationError:
                self.reply(401, {"error": "Unauthorized"})
                return
            except Exception:
                self.reply(503, {"error": "Authentication unavailable"})
                return
            # Local clients only; browser-originated requests remain blocked.
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
            except (ValueError, UnicodeError, OSError):
                self.reply(400, {"error": "Invalid request body"})
                return
            try:
                if "user_id" in request.parameters and request.parameters["user_id"] != user.user_id:
                    self.reply(403, {"error": "Forbidden identity"})
                    return
                response = assistant.for_user(user).handle(request)
                status = 200 if response.ok else {
                    "Unauthorized": 401, "Forbidden identity": 403, "Tool execution denied": 403
                }.get(response.error, 400)
                self.reply(status, asdict(response))
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
