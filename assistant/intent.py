"""Deterministic intent detection; only explicit /tool commands execute tools."""
import json
import re
from dataclasses import dataclass
from enum import Enum


class Intent(str, Enum):
    GREETING = "greeting"
    RECALL = "recall"
    TOOL = "tool"
    CHAT = "chat"


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict


@dataclass(frozen=True)
class IntentDecision:
    intent: Intent
    tool_call: ToolCall | None = None


class IntentDetector:
    def detect(self, message: str) -> IntentDecision:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("Message must be nonempty text")
        text = message.strip()
        if text.split(maxsplit=1)[0].casefold() == "/tool":
            parts = text.split(maxsplit=2)
            if len(parts) < 2 or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_.-]{0,63}", parts[1]):
                raise ValueError("Use /tool NAME followed by an optional JSON object")
            try:
                arguments = json.loads(parts[2]) if len(parts) == 3 else {}
            except ValueError as exc:
                raise ValueError("Tool arguments must be a JSON object") from exc
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be a JSON object")
            return IntentDecision(Intent.TOOL, ToolCall(parts[1], arguments))
        normalized = text.casefold().replace("ё", "е").strip(" .!?\n\t")
        if normalized in {"привет", "здравствуй", "здравствуйте", "добрый день", "hello", "hi"}:
            return IntentDecision(Intent.GREETING)
        if re.match(r"^(вспомни|найди|помнишь|remember|recall)\b", normalized) or normalized.startswith("что ты помнишь"):
            return IntentDecision(Intent.RECALL)
        return IntentDecision(Intent.CHAT)
