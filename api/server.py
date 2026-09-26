import json
import socket
import time
import os
import logging
import signal
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer

from assistant.core import Assistant
from assistant.models import Request
from config import Settings
from memory.sqlite import SQLiteMemory
from security.store import AuthenticationError
from runtime.errors import PublicError
from runtime.logging import event
from runtime.app import build_assistant
from api.v1 import V1API
from api.limiter import RateLimiter

MAX_BODY = 65536


def make_handler(assistant: Assistant):
    limiter = RateLimiter(int(os.getenv("ASSISTANT_RATE_LIMIT") or 60))
    v1 = V1API(assistant)

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
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            if status == 429:
                self.send_header("Retry-After", "60")
            event("http_response", status=status)
            if status == 401:
                self.send_header("WWW-Authenticate", "Bearer")
            self.end_headers()
            self.wfile.write(body)

        def authenticate(self):
            headers = self.headers.get_all("Authorization", [])
            if len(headers) != 1:
                raise AuthenticationError()
            parts = headers[0].split()
            if len(parts) != 2 or parts[0].casefold() != "bearer" or assistant.security is None:
                raise AuthenticationError()
            return assistant.security.authenticate(parts[1]), parts[1]

        def versioned(self):
            if not limiter.allow(self.client_address[0]):
                self.reply(429, {"error": "Rate limit exceeded"})
                return
            if self.headers.get("Origin") is not None:
                self.reply(403, {"error": "Browser origins are not allowed"})
                return
            try:
                user, token = self.authenticate()
                body = {}
                if self.command == "POST":
                    lengths = self.headers.get_all("Content-Length", [])
                    if (len(lengths) != 1 or self.headers.get("Transfer-Encoding") or
                            self.headers.get_content_type() != "application/json"):
                        raise PublicError("invalid_body", "Expected JSON with Content-Length", 400)
                    length = int(lengths[0])
                    if not 0 < length <= MAX_BODY:
                        raise PublicError("body_size", "Invalid body size", 413)
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError("Invalid request fields")
                result = v1.dispatch(self.command, self.path, user, body, token)
                self.reply(200, {"ok": True, "result": result})
            except AuthenticationError:
                self.reply(401, {"error": "Unauthorized"})
            except PublicError as exc:
                self.reply(exc.status, {"error": str(exc), "code": exc.code})
            except ValueError as exc:
                safe = str(exc)
                status = 403 if safe in ("Tool execution denied", "Confirmation rejected", "Session not found", "Task not found") else 400
                self.reply(status, {"error": safe if status == 403 else "Invalid request"})
            except Exception:
                self.reply(500, {"error": "Internal error"})

        def do_GET(self):
            if self.path in ("/health", "/api/v1/health"):
                self.reply(200, {"status": "ok"})
            elif self.path == "/api/v1/ready":
                try:
                    assistant.security._connection.execute("SELECT 1").fetchone()
                    self.reply(200, {"status": "ready"})
                except Exception:
                    self.reply(503, {"status": "unavailable"})
            elif self.path.startswith("/api/v1/"):
                self.versioned()
            else:
                self.reply(404, {"error": "Not found"})

        def do_POST(self):
            if self.path.startswith("/api/v1/"):
                self.versioned()
                return
            if not limiter.allow(self.client_address[0]):
                self.reply(429, {"error": "Rate limit exceeded"})
                return
            if self.path != "/requests":
                self.reply(404, {"error": "Not found"})
                return
            try:
                user, _ = self.authenticate()
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
                if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
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
                    "Unauthorized": 401, "Forbidden identity": 403, "Tool execution denied": 403, "Confirmation rejected": 403
                }.get(response.error, 400)
                self.reply(status, asdict(response))
            except Exception:
                self.reply(500, {"error": "Internal error"})

    return Handler


def main():
    settings = Settings.from_env()
    with SQLiteMemory(settings.database_path) as memory:
        assistant = build_assistant(memory, settings)
        with HTTPServer(("127.0.0.1", settings.port), make_handler(assistant)) as server:
            logging.basicConfig(level=logging.INFO, format="%(message)s")
            def stop(*_):
                threading.Thread(target=server.shutdown, daemon=True).start()
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, stop)
            print(f"Local API: http://127.0.0.1:{settings.port}")
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()
