import heapq
from memory.search import MemoryEntry, tokens
import sqlite3
from pathlib import Path


class SQLiteMemory:
    """Single-threaded local key/value storage with explicit lifetime."""

    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS memory (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self._connection.commit()

    def put(self, key: str, value: str) -> None:
        with self._connection:
            self._connection.execute(
                "INSERT INTO memory(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value)
            )

    def get(self, key: str) -> str | None:
        row = self._connection.execute("SELECT value FROM memory WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def delete(self, key: str) -> bool:
        with self._connection:
            return self._connection.execute("DELETE FROM memory WHERE key=?", (key,)).rowcount > 0

    def search(self, query: str, limit: int = 5) -> list[MemoryEntry]:
        if not isinstance(query, str) or len(query) > 4000:
            raise ValueError("Query must be text of at most 4000 characters")
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("Search limit must be between 1 and 10")
        terms = tokens(query)
        if not terms:
            return []

        def candidates():
            cursor = self._connection.execute("SELECT key, value FROM memory")
            try:
                for key, value in cursor:
                    score = 2 * len(terms & tokens(key)) + len(terms & tokens(value))
                    if score:
                        yield (-score, key, value)
            finally:
                cursor.close()

        # Keep only the best N rows; ties are ordered by key for repeatability.
        return [MemoryEntry(key, value) for _, key, value in heapq.nsmallest(limit, candidates())]

    def close(self) -> None:
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
