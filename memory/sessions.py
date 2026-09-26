"""Persistent user-scoped sessions; no authentication or global memory access."""
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4


class SessionError(ValueError):
    """Public identifier/ownership error without revealing other users' data."""


def validate_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
        raise SessionError(f"{name} must be 1..64 ASCII letters, digits, dots, underscores or hyphens; start with a letter or digit")
    return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class Session:
    user_id: str
    session_id: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class StoredMessage:
    message_id: int
    role: str
    content: str
    created_at: str


class SessionStore(Protocol):
    def create_session(self, user_id: str) -> Session: ...
    def get_session(self, user_id: str, session_id: str) -> Session: ...
    def recent_messages(self, user_id: str, session_id: str, limit: int = 100) -> list[StoredMessage]: ...
    def save_turn(self, user_id: str, session_id: str | None, message: str, reply: str) -> Session: ...
    def get_state(self, user_id: str, session_id: str, key: str) -> str | None: ...
    def set_state(self, user_id: str, session_id: str, key: str, value: str) -> None: ...
    def delete_state(self, user_id: str, session_id: str, key: str) -> bool: ...


class SQLiteSessionStore:
    """Shares SQLiteMemory's connection and lifetime; successful turns are atomic."""

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS sessions ("
                "user_id TEXT NOT NULL, session_id TEXT NOT NULL, "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
                "PRIMARY KEY (user_id, session_id), UNIQUE (session_id))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS session_messages ("
                "message_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "user_id TEXT NOT NULL, session_id TEXT NOT NULL, "
                "role TEXT NOT NULL CHECK (role IN ('user', 'assistant')), "
                "content TEXT NOT NULL, created_at TEXT NOT NULL, "
                "FOREIGN KEY (user_id, session_id) REFERENCES sessions(user_id, session_id))"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS session_messages_scope "
                "ON session_messages(user_id, session_id, message_id)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS session_state ("
                "user_id TEXT NOT NULL, session_id TEXT NOT NULL, "
                "key TEXT NOT NULL, value TEXT NOT NULL, updated_at TEXT NOT NULL, "
                "PRIMARY KEY (user_id, session_id, key), "
                "FOREIGN KEY (user_id, session_id) "
                "REFERENCES sessions(user_id, session_id) ON DELETE CASCADE)"
            )

    def _insert_session(self, user_id: str) -> Session:
        now = utc_now()
        session = Session(user_id, str(uuid4()), now, now)
        self._connection.execute(
            "INSERT INTO sessions(user_id, session_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session.user_id, session.session_id, session.created_at, session.updated_at),
        )
        return session

    def create_session(self, user_id: str) -> Session:
        validate_id(user_id, "user_id")
        with self._connection:
            return self._insert_session(user_id)

    def get_session(self, user_id: str, session_id: str) -> Session:
        validate_id(user_id, "user_id")
        validate_id(session_id, "session_id")
        row = self._connection.execute(
            "SELECT user_id, session_id, created_at, updated_at FROM sessions "
            "WHERE user_id = ? AND session_id = ?", (user_id, session_id),
        ).fetchone()
        if row is None:
            raise SessionError("Session not found")
        return Session(*row)

    def recent_messages(self, user_id: str, session_id: str, limit: int = 100) -> list[StoredMessage]:
        self.get_session(user_id, session_id)
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("History candidate limit must be between 1 and 1000")
        rows = self._connection.execute(
            "SELECT message_id, role, substr(content, 1, 4000), created_at FROM session_messages "
            "WHERE user_id = ? AND session_id = ? ORDER BY message_id DESC LIMIT ?",
            (user_id, session_id, limit),
        ).fetchall()
        return [StoredMessage(*row) for row in reversed(rows)]

    @staticmethod
    def _state_key(key: str) -> str:
        if not isinstance(key, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", key
        ):
            raise ValueError("State key must be 1..64 safe ASCII characters")
        return key

    def get_state(self, user_id: str, session_id: str, key: str) -> str | None:
        self.get_session(user_id, session_id)
        key = self._state_key(key)
        row = self._connection.execute(
            "SELECT value FROM session_state "
            "WHERE user_id = ? AND session_id = ? AND key = ?",
            (user_id, session_id, key),
        ).fetchone()
        return row[0] if row else None

    def set_state(self, user_id: str, session_id: str, key: str, value: str) -> None:
        self.get_session(user_id, session_id)
        key = self._state_key(key)

        if not isinstance(value, str) or not value or len(value) > 16000:
            raise ValueError("State value must contain 1..16000 characters")

        now = utc_now()

        with self._connection:
            self._connection.execute(
                "INSERT INTO session_state(user_id, session_id, key, value, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, session_id, key) "
                "DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (user_id, session_id, key, value, now),
            )

    def delete_state(self, user_id: str, session_id: str, key: str) -> bool:
        self.get_session(user_id, session_id)
        key = self._state_key(key)

        with self._connection:
            return self._connection.execute(
                "DELETE FROM session_state "
                "WHERE user_id = ? AND session_id = ? AND key = ?",
                (user_id, session_id, key),
            ).rowcount > 0

    def save_turn(self, user_id: str, session_id: str | None, message: str, reply: str) -> Session:
        validate_id(user_id, "user_id")
        if session_id is not None:
            validate_id(session_id, "session_id")
        if any(not isinstance(text, str) or not text.strip() for text in (message, reply)):
            raise ValueError("A turn requires nonempty user and assistant messages")
        with self._connection:
            session = self._insert_session(user_id) if session_id is None else self.get_session(user_id, session_id)
            now = max(utc_now(), session.updated_at)
            self._connection.executemany(
                "INSERT INTO session_messages(user_id, session_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [(user_id, session.session_id, "user", message, now),
                 (user_id, session.session_id, "assistant", reply, now)],
            )
            self._connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE user_id = ? AND session_id = ?",
                (now, user_id, session.session_id),
            )
            return Session(user_id, session.session_id, session.created_at, now)
