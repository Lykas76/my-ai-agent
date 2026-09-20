"""Authenticated users' fact memory, separate from legacy shared facts."""
import heapq
from memory.search import MemoryEntry, tokens


class UserMemory:
    def __init__(self, memory, user_id: str):
        self._connection = memory._connection
        self.user_id = user_id
        self.sessions = memory.sessions
        self.security = memory.security

    @staticmethod
    def initialize(connection):
        with connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS user_memory (user_id TEXT NOT NULL REFERENCES users(user_id), "
                "key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(user_id, key))"
            )

    def put(self, key: str, value: str) -> None:
        with self._connection:
            self._connection.execute(
                "INSERT INTO user_memory VALUES (?, ?, ?) "
                "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value", (self.user_id, key, value)
            )

    def get(self, key: str) -> str | None:
        row = self._connection.execute(
            "SELECT value FROM user_memory WHERE user_id=? AND key=?", (self.user_id, key)
        ).fetchone()
        return row[0] if row else None

    def delete(self, key: str) -> bool:
        with self._connection:
            return self._connection.execute(
                "DELETE FROM user_memory WHERE user_id=? AND key=?", (self.user_id, key)
            ).rowcount > 0

    def search(self, query: str, limit: int = 5) -> list[MemoryEntry]:
        if not isinstance(query, str) or len(query) > 4000 or type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("Invalid search query or limit")
        terms = tokens(query)
        if not terms:
            return []
        def candidates():
            cursor = self._connection.execute("SELECT key, value FROM user_memory WHERE user_id=?", (self.user_id,))
            try:
                for key, value in cursor:
                    score = 2 * len(terms & tokens(key)) + len(terms & tokens(value))
                    if score:
                        yield (-score, key, value)
            finally:
                cursor.close()
        return [MemoryEntry(key, value) for _, key, value in heapq.nsmallest(limit, candidates())]
