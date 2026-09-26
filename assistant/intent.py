"""Deterministic intent detection with safe read-only natural-language routing."""
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


def _requested_count(text: str, default: int = 10) -> int:
    for match in re.finditer(r"\b(\d{1,2})\b", text):
        value = int(match.group(1))
        if 1 <= value <= 20:
            return value
    return default


def _gmail_search_value(value: str) -> str:
    value = value.strip(" .!?\"'??")
    value = value.replace('"', "")
    if not value:
        return ""
    return f'"{value}"' if " " in value else value


def _gmail_search_route(normalized: str, count: int) -> ToolCall | None:
    # Russian: "... ?? Google"
    sender = re.search(
        r"(?:^|\s)\u043e\u0442\s+(.+)$",
        normalized,
    )
    if sender:
        value = _gmail_search_value(sender.group(1))
        if value:
            return ToolCall(
                "gmail.search",
                {
                    "query": f"from:{value}",
                    "max_results": count,
                },
            )

    # Russian: "... ? ????? Security alert" / "... ???? Security alert"
    subject = re.search(
        r"(?:\u0441\s+\u0442\u0435\u043c\u043e\u0439|\u0442\u0435\u043c\u0430)\s+(.+)$",
        normalized,
    )
    if subject:
        value = _gmail_search_value(subject.group(1))
        if value:
            return ToolCall(
                "gmail.search",
                {
                    "query": f"subject:{value}",
                    "max_results": count,
                },
            )

    # Russian: "????? ? ????? Railway"
    prefixes = (
        "\u043f\u043e\u0438\u0449\u0438 \u0432 \u043f\u043e\u0447\u0442\u0435 ",
        "\u043d\u0430\u0439\u0434\u0438 \u0432 \u043f\u043e\u0447\u0442\u0435 ",
        "search mail for ",
        "search email for ",
    )

    for prefix in prefixes:
        if normalized.startswith(prefix):
            value = normalized[len(prefix):].strip()
            if value:
                return ToolCall(
                    "gmail.search",
                    {
                        "query": value,
                        "max_results": count,
                    },
                )

    return None


def _gmail_readonly_route(normalized: str) -> ToolCall | None:
    mail_terms = (
        "\u043f\u0438\u0441\u044c\u043c",
        "\u043f\u0438\u0441\u0435\u043c",
        "\u043f\u043e\u0447\u0442",
        "email",
        "e-mail",
        "mail",
        "message",
    )

    if not any(term in normalized for term in mail_terms):
        return None

    count = _requested_count(normalized)

    search_call = _gmail_search_route(normalized, count)
    if search_call is not None:
        return search_call

    unread_terms = (
        "\u043d\u0435\u043f\u0440\u043e\u0447\u0438\u0442\u0430\u043d",
        "unread",
    )

    if any(term in normalized for term in unread_terms):
        return ToolCall(
            "gmail.list_unread",
            {"max_results": count},
        )

    recent_terms = (
        "\u043f\u043e\u0441\u043b\u0435\u0434\u043d",
        "\u043d\u0435\u0434\u0430\u0432\u043d",
        "recent",
        "latest",
        "last",
    )

    if any(term in normalized for term in recent_terms):
        return ToolCall(
            "gmail.list_recent",
            {"max_results": count},
        )

    return None


class IntentDetector:
    def detect(self, message: str) -> IntentDecision:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("Message must be nonempty text")

        text = message.strip()

        if text.split(maxsplit=1)[0].casefold() == "/tool":
            parts = text.split(maxsplit=2)

            if (
                len(parts) < 2
                or not re.fullmatch(
                    r"[a-zA-Z][a-zA-Z0-9_.-]{0,63}",
                    parts[1],
                )
            ):
                raise ValueError(
                    "Use /tool NAME followed by an optional JSON object"
                )

            try:
                arguments = (
                    json.loads(parts[2])
                    if len(parts) == 3
                    else {}
                )
            except ValueError as exc:
                raise ValueError(
                    "Tool arguments must be a JSON object"
                ) from exc

            if not isinstance(arguments, dict):
                raise ValueError(
                    "Tool arguments must be a JSON object"
                )

            return IntentDecision(
                Intent.TOOL,
                ToolCall(parts[1], arguments),
            )

        normalized = (
            text.casefold()
            .replace("\u0451", "\u0435")
            .strip(" .!?\n\t")
        )

        greetings = {
            "\u043f\u0440\u0438\u0432\u0435\u0442",
            "\u0437\u0434\u0440\u0430\u0432\u0441\u0442\u0432\u0443\u0439",
            "\u0437\u0434\u0440\u0430\u0432\u0441\u0442\u0432\u0443\u0439\u0442\u0435",
            "\u0434\u043e\u0431\u0440\u044b\u0439 \u0434\u0435\u043d\u044c",
            "hello",
            "hi",
        }

        if normalized in greetings:
            return IntentDecision(Intent.GREETING)

        gmail_call = _gmail_readonly_route(normalized)

        if gmail_call is not None:
            return IntentDecision(
                Intent.TOOL,
                gmail_call,
            )

        recall_prefixes = (
            "\u0432\u0441\u043f\u043e\u043c\u043d\u0438",
            "\u043d\u0430\u0439\u0434\u0438",
            "\u043f\u043e\u043c\u043d\u0438\u0448\u044c",
            "remember",
            "recall",
        )

        if (
            normalized.startswith(recall_prefixes)
            or normalized.startswith(
                "\u0447\u0442\u043e \u0442\u044b "
                "\u043f\u043e\u043c\u043d\u0438\u0448\u044c"
            )
        ):
            return IntentDecision(Intent.RECALL)

        return IntentDecision(Intent.CHAT)
