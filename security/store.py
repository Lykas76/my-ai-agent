"""SQLite security state. Credentials are generated randomly and stored as SHA-256 only."""
import hashlib
import hmac
import re
import secrets
import sqlite3
from dataclasses import dataclass, field
from uuid import uuid4

from memory.sessions import utc_now, validate_id

PERMISSIONS = frozenset({"read", "write", "sensitive"})


class AuthenticationError(ValueError):
    def __init__(self):
        super().__init__("Unauthorized")


class IdentityError(ValueError):
    def __init__(self):
        super().__init__("Forbidden identity")


def validate_tool_name(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", name):
        raise ValueError("Invalid tool name")
    return name


@dataclass(frozen=True)
class User:
    user_id: str
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class IssuedToken:
    user_id: str
    token_id: str
    token: str = field(repr=False)


@dataclass(frozen=True)
class AccessContext:
    """Constructed by trusted application code, never deserialized from a request."""
    user_id: str
    store: "SecurityStore" = field(repr=False)


class SecurityStore:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        with connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, "
                "status TEXT NOT NULL CHECK(status IN ('active','disabled')), "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS api_tokens (token_id TEXT PRIMARY KEY, "
                "user_id TEXT NOT NULL REFERENCES users(user_id), token_hash TEXT NOT NULL, "
                "created_at TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS tool_permissions (user_id TEXT NOT NULL REFERENCES users(user_id), "
                "tool TEXT NOT NULL, permission TEXT NOT NULL CHECK(permission IN ('read','write','sensitive')), "
                "PRIMARY KEY(user_id, tool, permission))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS security_audit (audit_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "timestamp TEXT NOT NULL, user_id TEXT, tool TEXT NOT NULL, requested_permission TEXT, "
                "decision TEXT NOT NULL CHECK(decision IN ('allow','deny','confirmation_required')))"
            )

    def create_user(self, user_id: str | None = None) -> User:
        # Explicit IDs allow a local administrator to adopt verified legacy sessions.
        user_id = str(uuid4()) if user_id is None else validate_id(user_id, "user_id")
        now = utc_now()
        with self._connection:
            self._connection.execute("INSERT INTO users VALUES (?, 'active', ?, ?)", (user_id, now, now))
        return User(user_id, "active", now, now)

    def get_user(self, user_id: str) -> User | None:
        row = self._connection.execute(
            "SELECT user_id, status, created_at, updated_at FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        return User(*row) if row else None

    def set_status(self, user_id: str, status: str) -> None:
        if status not in ("active", "disabled"):
            raise ValueError("Invalid user status")
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE users SET status=?, updated_at=? WHERE user_id=?", (status, utc_now(), user_id)
            )
            if cursor.rowcount != 1:
                raise ValueError("User not found")

    def issue_token(self, user_id: str) -> IssuedToken:
        user = self.get_user(user_id)
        if user is None or user.status != "active":
            raise ValueError("Active user required")
        token_id = uuid4().hex
        raw = token_id + "." + secrets.token_urlsafe(32)
        digest = hashlib.sha256(raw.encode("ascii")).hexdigest()
        with self._connection:
            self._connection.execute(
                "INSERT INTO api_tokens(token_id, user_id, token_hash, created_at) VALUES (?, ?, ?, ?)",
                (token_id, user_id, digest, utc_now()),
            )
        return IssuedToken(user_id, token_id, raw)

    def revoke_token(self, token_id: str) -> None:
        with self._connection:
            self._connection.execute("UPDATE api_tokens SET revoked=1 WHERE token_id=?", (token_id,))

    def authenticate(self, token: str) -> User:
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}\.[A-Za-z0-9_-]{43}", token):
            raise AuthenticationError()
        row = self._connection.execute(
            "SELECT u.user_id, u.status, u.created_at, u.updated_at, t.token_hash, t.revoked "
            "FROM api_tokens t JOIN users u ON u.user_id=t.user_id WHERE t.token_id=?", (token[:32],)
        ).fetchone()
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        valid = hmac.compare_digest(digest, row[4] if row else "0" * 64)
        if not valid or row is None or row[5] or row[1] != "active":
            raise AuthenticationError()
        return User(*row[:4])

    def grant(self, user_id: str, tool: str, permission: str) -> None:
        validate_tool_name(tool)
        if permission not in PERMISSIONS or self.get_user(user_id) is None:
            raise ValueError("Known user and permission required")
        with self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO tool_permissions VALUES (?, ?, ?)", (user_id, tool, permission)
            )

    def revoke_permission(self, user_id: str, tool: str, permission: str) -> None:
        with self._connection:
            self._connection.execute(
                "DELETE FROM tool_permissions WHERE user_id=? AND tool=? AND permission=?",
                (user_id, tool, permission),
            )

    def is_allowed(self, user_id: str, tool: str, permission: str | None) -> bool:
        if permission not in PERMISSIONS:
            return False
        return self._connection.execute(
            "SELECT 1 FROM tool_permissions p JOIN users u ON u.user_id=p.user_id "
            "WHERE p.user_id=? AND p.tool=? AND p.permission=? AND u.status='active'",
            (user_id, tool, permission),
        ).fetchone() is not None

    def audit(self, user_id: str, tool: str, permission: str | None, decision: str) -> None:
        # Callers supply registered metadata only, never arguments, tokens or request text.
        with self._connection:
            self._connection.execute(
                "INSERT INTO security_audit(timestamp, user_id, tool, requested_permission, decision) "
                "VALUES (?, ?, ?, ?, ?)", (utc_now(), user_id, tool, permission, decision),
            )
