"""Explicit application factory shared by API, scheduler and Telegram."""
import os
from assistant.core import Assistant
from assistant.providers import LocalProvider
from assistant.openai_provider import OpenAICompatibleProvider
from adapters.builtin import register_builtin
from tools.registry import ToolRegistry


def build_assistant(memory, settings):
    mode = os.getenv("ASSISTANT_PROVIDER") or "local"
    if mode not in ("local", "openai"):
        raise ValueError("ASSISTANT_PROVIDER must be local or openai")
    provider = OpenAICompatibleProvider.from_env() if mode == "openai" else LocalProvider()
    registry = ToolRegistry(confirmation_ttl_seconds=settings.confirmation_ttl_seconds)

    gmail_enabled = (os.getenv("ASSISTANT_GMAIL_ENABLED") or "").strip().lower() in {
        "1", "true", "yes", "on"
    }

    register_builtin(
        registry,
        memory,
        include_gmail_stub=not gmail_enabled,
    )

    if gmail_enabled:
        from adapters.gmail import register_gmail_readonly
        register_gmail_readonly(registry)

    return Assistant(memory, registry, provider, history_policy=settings.history_policy)
