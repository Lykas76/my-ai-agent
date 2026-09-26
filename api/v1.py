"""Versioned client contract. Identity is always supplied by trusted authentication."""
from dataclasses import asdict
from assistant.models import Request
from scheduler.store import Scheduler
from security.store import AuthenticationError
from runtime.errors import PublicError


def fields(body, allowed, required=()):
    if not isinstance(body, dict) or set(body) - set(allowed) or set(required) - set(body):
        raise ValueError("Invalid request fields")


class V1API:
    def __init__(self, assistant):
        self.assistant = assistant
        self.memory = assistant.memory
        self.scheduler = Scheduler(self.memory, assistant.tools)

    def dispatch(self, method, path, user, body=None, token=None):
        body = {} if body is None else body
        current = self.memory.security.get_user(user.user_id)
        if current is None or current.status != "active":
            raise AuthenticationError()
        uid = user.user_id
        parts = path.removeprefix("/api/v1/").split("/")
        if parts in (["auth", "login"], ["status"]) and method in ("GET", "POST"):
            fields(body, ())
            row = self.memory._connection.execute("SELECT expires_at FROM api_tokens WHERE token_id=? AND user_id=?",
                                                  ((token or "")[:32],uid)).fetchone()
            return {"user_id": uid, "status": "active", "token_expires_at": row[0] if row else None}
        if parts == ["auth", "token"] and method == "POST":
            fields(body, ())
            if token is None or self.memory.security.authenticate(token).user_id != uid:
                raise AuthenticationError()
            # Rotation must be authenticated. Raw token is returned once, never persisted.
            issued = self.memory.security.rotate_token(token)
            return {"user_id": uid, "token_id": issued.token_id, "token": issued.token}
        if parts == ["auth", "revoke"] and method == "POST":
            fields(body, ())
            self.memory.security.revoke_token((token or "")[:32])
            return {"revoked": True}
        if parts == ["chat"] and method == "POST":
            fields(body, ("message", "session_id"), ("message",))
            response = self.assistant.for_user(user).handle(Request("dialogue", body))
            if not response.ok:
                raise PublicError("request_failed", response.error, 403 if response.error in
                                  ("Tool execution denied", "Confirmation rejected", "Session not found") else 400)
            return response.result
        if parts == ["sessions"]:
            fields(body, ())
            if method == "POST":
                return asdict(self.memory.sessions.create_session(uid))
            if method == "GET":
                rows = self.memory._connection.execute(
                    "SELECT session_id,created_at,updated_at FROM sessions WHERE user_id=? ORDER BY updated_at DESC LIMIT 100", (uid,))
                return {"items": [dict(zip(("session_id","created_at","updated_at"),row)) for row in rows]}
        if len(parts) == 3 and parts[0] == "sessions" and parts[2] == "history" and method == "GET":
            return {"items": [asdict(item) for item in self.memory.sessions.recent_messages(uid,parts[1],100)]}
        if parts == ["confirmations"] and method == "GET":
            rows = self.memory._connection.execute(
                "SELECT confirmation_id FROM confirmations WHERE user_id=? AND status IN ('pending','approved') "
                "ORDER BY created_at LIMIT 100", (uid,)).fetchall()
            items = [self.memory.security.confirmations.get(uid,row[0]) for row in rows]
            return {"items": [item for item in items if item["status"] in ("pending","approved")]}
        if len(parts) in (2,3) and parts[0] == "confirmations":
            fields(body, ())
            operation = parts[2] if len(parts) == 3 else "get"
            if (operation == "get" and method == "GET") or (operation in ("approve","cancel") and method == "POST"):
                return self.assistant.tools._confirm(
                    self.assistant.for_user(user).access, operation, uid, parts[1])
            if operation == "execute" and method == "POST":
                confirmation = self.memory.security.confirmations.get(uid,parts[1])
                value = self.assistant.tools.execute_result(confirmation["tool"],confirmation["arguments"],
                    access=self.assistant.for_user(user).access, confirmation_id=parts[1],session_id=confirmation["session_id"])
                return asdict(value)
        if parts == ["notifications"] and method == "GET":
            rows = self.memory._connection.execute(
                "SELECT key,value FROM user_memory WHERE user_id=? AND key LIKE 'notification:%' ORDER BY key DESC LIMIT 100", (uid,))
            return {"items": [{"id": row[0], "text": row[1]} for row in rows]}
        if parts == ["reminders"]:
            if method == "GET":
                return {"items": [item for item in self.scheduler.list(uid) if item["tool"] == "reminders.notify"]}
            if method == "POST":
                fields(body, ("text","next_run_at","interval_seconds"), ("text","next_run_at"))
                payload = {"tool": "reminders.notify", "arguments": {"text": body["text"]}, "next_run_at": body["next_run_at"]}
                if "interval_seconds" in body:
                    payload["interval_seconds"] = body["interval_seconds"]
                return self.scheduler.create(uid,payload)
        if parts == ["tasks"]:
            if method == "GET":
                return {"items": self.scheduler.list(uid)}
            if method == "POST":
                return self.scheduler.create(uid,body)
        if len(parts) == 3 and parts[0] == "tasks":
            if parts[2] == "runs" and method == "GET":
                return {"items": self.scheduler.runs(uid,parts[1])}
            if parts[2] == "enabled" and method == "POST":
                fields(body, ("enabled",), ("enabled",))
                return self.scheduler.enable(uid,parts[1],body["enabled"])
        raise PublicError("not_found", "Not found", 404)
