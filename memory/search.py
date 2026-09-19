"""Bounded lexical retrieval shared by local storage adapters."""
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

STOP_WORDS = frozenset(
    "и в во на о об а но я ты мы вы он она это что как где когда мне меня мой моя мое мои "
    "у по из для про ли же бы не да с со к ко за от до ты помнишь вспомни найди "
    "the a an is are i you my me about what where how do remember recall".split()
)


def tokens(text: str) -> set[str]:
    words = re.findall(r"[^\W_]+", text.casefold().replace("ё", "е"))
    return {word for word in words if word not in STOP_WORDS}


@dataclass(frozen=True)
class MemoryEntry:
    key: str
    value: str


@runtime_checkable
class SearchableMemory(Protocol):
    def search(self, query: str, limit: int = 5) -> list[MemoryEntry]: ...
