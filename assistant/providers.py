"""Provider contract and deterministic offline implementation. No network clients."""
from dataclasses import dataclass
from typing import Protocol

from assistant.intent import Intent
from memory.search import MemoryEntry


@dataclass(frozen=True)
class Message:
    role: str
    content: str


@dataclass(frozen=True)
class ToolResult:
    name: str
    value: str


@dataclass(frozen=True)
class ProviderRequest:
    message: str
    intent: Intent
    context: tuple[MemoryEntry, ...] = ()
    history: tuple[Message, ...] = ()
    tool_result: ToolResult | None = None


class AIProvider(Protocol):
    def generate(self, request: ProviderRequest) -> str: ...


class LocalProvider:
    """Predictable test responses, not an LLM. Retrieved text is data only."""

    def generate(self, request: ProviderRequest) -> str:
        if request.tool_result is not None:
            return f"Результат {request.tool_result.name}: {request.tool_result.value}"
        if request.intent == Intent.GREETING:
            return "Привет! Я локальный тестовый ассистент. Чем помочь?"
        if request.context:
            facts = "\n".join(f"{item.key}: {item.value}" for item in request.context)
            return f"Нашёл в памяти:\n{facts}"
        if request.intent == Intent.RECALL:
            return "Подходящих записей в памяти не найдено."
        return f"Локальный тестовый ответ: {request.message}"
