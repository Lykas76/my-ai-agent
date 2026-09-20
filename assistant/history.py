"""Deterministic, bounded selection of recent session context."""
from dataclasses import dataclass

from assistant.providers import Message
from memory.search import tokens
from memory.sessions import StoredMessage


@dataclass(frozen=True)
class HistoryPolicy:
    max_messages: int = 20
    max_chars: int = 8000
    candidate_messages: int = 100

    def __post_init__(self):
        for name, low, high in (("max_messages", 1, 100), ("max_chars", 1, 100000),
                                ("candidate_messages", 1, 1000)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer between {low} and {high}")
        if self.candidate_messages < self.max_messages:
            raise ValueError("candidate_messages must be at least max_messages")

    def select(self, rows: list[StoredMessage], query: str) -> tuple[Message, ...]:
        # Input is chronological; always favour the latest exchange, then word
        # overlap, then recency. Only the bounded candidate window is considered.
        rows = rows[-self.candidate_messages:]
        terms = tokens(query)
        latest = {row.message_id for row in rows[-2:]}
        ranked = sorted(rows, key=lambda row: (
            row.message_id in latest, len(terms & tokens(row.content)), row.message_id
        ), reverse=True)
        selected = []
        remaining = self.max_chars
        for row in ranked[:self.max_messages]:
            content = row.content[:min(4000, remaining)]
            if not content:
                break
            selected.append((row.message_id, Message(row.role, content)))
            remaining -= len(content)
        return tuple(message for _, message in sorted(selected, key=lambda item: item[0]))
