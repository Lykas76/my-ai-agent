"""Persistent one-use confirmations. A committed consume precedes any side effect."""
import hashlib
import hmac
import json
import re
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta
from uuid import uuid4

from memory.sessions import utc_now

DEFAULT_TTL = 120


class ConfirmationRejected(ValueError):
    def __init__(self):
        super().__init__("Confirmation rejected")


def validate_ttl(seconds):
    if type(seconds) is not int or not 1 <= seconds <= 3600:
        raise ValueError("Confirmation TTL must be an integer between 1 and 3600 seconds")
    return seconds


def canonical_arguments(arguments):
    """Strict bounded JSON, with credential-shaped values rejected, never redacted."""
    secret_keys = ("password", "secret", "token", "authorization", "credential", "api_key", "private_key", "cookie")
    def check(value, depth=0):
        if depth > 8:
            raise ConfirmationRejected()
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str) or any(word in key.casefold().replace("-", "_") for word in secret_keys):
                    raise ConfirmationRejected()
                check(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                check(item, depth + 1)
        elif isinstance(value, str):
            if re.search(r"(?i)bearer\s|[0-9a-f]{32}\.[A-Za-z0-9_-]{43}|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN .*PRIVATE KEY", value):
                raise ConfirmationRejected()
        elif value is not None and type(value) not in (bool, int, float):
            raise ConfirmationRejected()
    if not isinstance(arguments, dict):
        raise ConfirmationRejected()
    try:
        check(arguments)
        canonical = json.dumps(arguments, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(canonical) > 8192:
            raise ConfirmationRejected()
        return canonical, hashlib.sha256(canonical.encode("ascii")).hexdigest()
    except (TypeError, ValueError, RecursionError) as exc:
        raise ConfirmationRejected() from exc


def validate_schema(arguments, schema):
    """Only explicitly declared non-secret primitives/enumerations can be persisted.

    Free-form strings are intentionally unsupported. Use enum values or numeric
    references; resolve credentials inside the trusted handler, never in arguments.
    """
    schema = {"confirmed": bool} if schema is None else schema
    def matches(value, rule):
        if rule in (bool, int, float):
            return type(value) is rule
        if isinstance(rule, tuple):
            return isinstance(value, str) and value in rule
        if isinstance(rule, dict):
            return isinstance(value, dict) and all(k in rule and matches(v, rule[k]) for k, v in value.items())
        return False
    if not isinstance(schema, dict) or not isinstance(arguments, dict) or not matches(arguments, schema):
        raise ConfirmationRejected()


class ConfirmationStore:
    COLUMNS = ("confirmation_id", "user_id", "session_id", "tool", "permission", "arguments_json",
               "arguments_hash", "status", "created_at", "expires_at", "approved_at", "consumed_at")

    def __init__(self, security):
        self.security = security
        self.connection = security._connection
        with self.connection:
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS confirmations ("
                "confirmation_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id), "
                "session_id TEXT, tool TEXT NOT NULL, permission TEXT NOT NULL CHECK(permission='sensitive'), "
                "arguments_json TEXT NOT NULL, arguments_hash TEXT NOT NULL, "
                "status TEXT NOT NULL CHECK(status IN ('pending','approved','consumed','expired','cancelled')), "
                "created_at TEXT NOT NULL, expires_at TEXT NOT NULL, approved_at TEXT, consumed_at TEXT, "
                "FOREIGN KEY(user_id, session_id) REFERENCES sessions(user_id, session_id))"
            )
            self.connection.execute("CREATE INDEX IF NOT EXISTS confirmations_owner ON confirmations(user_id, confirmation_id)")

    @contextmanager
    def _transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def _owned(self, user_id, confirmation_id):
        if not isinstance(confirmation_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", confirmation_id):
            return None
        row = self.connection.execute(
            "SELECT " + ",".join(self.COLUMNS) + " FROM confirmations WHERE confirmation_id=? AND user_id=?",
            (confirmation_id, user_id),
        ).fetchone()
        return dict(zip(self.COLUMNS, row)) if row else None

    def _event(self, user_id, row, event):
        decision = "deny" if event in ("confirmation_expired", "confirmation_rejected") else "allow"
        if event == "confirmation_created":
            decision = "confirmation_required"
        self.security._audit_in_transaction(
            user_id, row["tool"] if row else "<unknown>", row["permission"] if row else None,
            decision, event, row["confirmation_id"] if row else None,
        )

    def _active(self, user_id):
        user = self.security.get_user(user_id)
        return user is not None and user.status == "active"

    def _session_owned(self, user_id, session_id):
        return session_id is None or self.connection.execute(
            "SELECT 1 FROM sessions WHERE user_id=? AND session_id=?", (user_id, session_id)
        ).fetchone() is not None

    @staticmethod
    def _public(row):
        result = dict(row)
        result["arguments"] = json.loads(result.pop("arguments_json"))
        return result

    def create(self, user_id, tool, permission, arguments, *, session_id=None, ttl=DEFAULT_TTL, new_session=False):
        validate_ttl(ttl)
        canonical, digest = canonical_arguments(arguments)
        result = None
        with self._transaction():
            if permission != "sensitive" or not self.security.is_allowed(user_id, tool, permission) or not self._session_owned(user_id, session_id):
                self._event(user_id, None, "confirmation_rejected")
            else:
                now = utc_now()
                if new_session and session_id is None:
                    session_id = str(uuid4())
                    self.connection.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (user_id, session_id, now, now))
                result = dict(zip(self.COLUMNS, (
                    secrets.token_urlsafe(32), user_id, session_id, tool, permission, canonical, digest,
                    "pending", now, (datetime.fromisoformat(now) + timedelta(seconds=ttl)).isoformat(timespec="microseconds"), None, None,
                )))
                self.connection.execute(
                    "INSERT INTO confirmations (" + ",".join(self.COLUMNS) + ") VALUES (" + ",".join("?" for _ in self.COLUMNS) + ")",
                    tuple(result[column] for column in self.COLUMNS),
                )
                self._event(user_id, result, "confirmation_created")
        if result is None:
            raise ConfirmationRejected()
        return self._public(result)

    def _operate(self, user_id, confirmation_id, operation, *, tool=None, permission=None, arguments=None, session_id=None):
        result = None
        with self._transaction():
            row = self._owned(user_id, confirmation_id)
            now = utc_now()
            if row and row["status"] in ("pending", "approved") and now >= row["expires_at"]:
                self.connection.execute("UPDATE confirmations SET status='expired' WHERE confirmation_id=?", (confirmation_id,))
                row["status"] = "expired"
                self._event(user_id, row, "confirmation_expired")
            if row and self._active(user_id):
                if operation == "get":
                    result = row
                elif operation == "cancel" and row["status"] in ("pending", "approved"):
                    row["status"] = "cancelled"
                    result = row
                elif operation == "approve" and row["status"] == "pending" and self.security.is_allowed(user_id, row["tool"], row["permission"]) and self._session_owned(user_id, row["session_id"]):
                    row.update(status="approved", approved_at=now)
                    result = row
                elif operation == "consume" and row["status"] == "approved":
                    try:
                        canonical, digest = canonical_arguments(arguments)
                        stored_hash = hashlib.sha256(row["arguments_json"].encode("ascii")).hexdigest()
                        bound = (row["tool"] == tool and row["permission"] == permission == "sensitive"
                                 and row["session_id"] == session_id
                                 and hmac.compare_digest(row["arguments_hash"], digest)
                                 and hmac.compare_digest(row["arguments_hash"], stored_hash)
                                 and row["arguments_json"] == canonical)
                    except (ValueError, UnicodeError):
                        bound = False
                    if bound and self.security.is_allowed(user_id, tool, permission) and self._session_owned(user_id, session_id):
                        row.update(status="consumed", consumed_at=now)
                        result = row
            if result is None:
                self._event(user_id, row, "confirmation_rejected")
            elif operation != "get":
                self.connection.execute(
                    "UPDATE confirmations SET status=?, approved_at=?, consumed_at=? WHERE confirmation_id=? AND user_id=?",
                    (row["status"], row["approved_at"], row["consumed_at"], confirmation_id, user_id),
                )
                self._event(user_id, row, {"approve": "confirmation_approved", "cancel": "confirmation_cancelled", "consume": "confirmation_consumed"}[operation])
        # Reject after commit so rejection/expiry events survive; no tool has run yet.
        if result is None:
            raise ConfirmationRejected()
        return self._public(result)

    def get(self, user_id, confirmation_id):
        return self._operate(user_id, confirmation_id, "get")

    def approve(self, user_id, confirmation_id):
        return self._operate(user_id, confirmation_id, "approve")

    def cancel(self, user_id, confirmation_id):
        return self._operate(user_id, confirmation_id, "cancel")

    def reject(self, user_id, confirmation_id=None):
        return self._operate(user_id, confirmation_id, "reject")

    def consume(self, user_id, confirmation_id, tool, permission, arguments, session_id=None):
        return self._operate(user_id, confirmation_id, "consume", tool=tool, permission=permission,
                             arguments=arguments, session_id=session_id)
