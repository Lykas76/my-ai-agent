"""One dialogue turn: validate, classify, retrieve, execute, generate."""
from dataclasses import asdict, dataclass

from assistant.intent import Intent, IntentDetector
from assistant.providers import AIProvider, LocalProvider, Message, ProviderRequest, ToolResult
from memory.base import Memory
from memory.search import MemoryEntry, SearchableMemory
from tools.registry import ToolDenied, ToolRegistry

MAX_MESSAGE = 4000
MAX_HISTORY = 20
MAX_CONTEXT = 5
MAX_CONTEXT_KEY = 200
MAX_CONTEXT_VALUE = 1000
MAX_TOOL_RESULT = 4000


class DialogueError(ValueError):
    """A safe public error message for a failed dialogue turn."""


@dataclass(frozen=True)
class DialogueResponse:
    reply: str
    intent: str
    context_keys: tuple[str, ...]
    tool_used: str | None = None


class DialogueHandler:
    def __init__(self, memory: Memory, tools: ToolRegistry, provider: AIProvider | None = None):
        self.memory = memory
        self.tools = tools
        self.provider = provider if provider is not None else LocalProvider()
        self.detector = IntentDetector()

    def handle(self, parameters: dict) -> dict:
        if not isinstance(parameters, dict) or set(parameters) - {"message", "history"}:
            raise ValueError("Expected message and optional history")
        message = self._text(parameters.get("message"))
        history = self._history(parameters.get("history", []))
        decision = self.detector.detect(message)
        context = ()
        # A greeting or tool invocation does not need to disclose stored memories.
        if decision.intent in {Intent.CHAT, Intent.RECALL} and isinstance(self.memory, SearchableMemory):
            try:
                entries = self.memory.search(message, limit=MAX_CONTEXT)
                context = tuple(
                    MemoryEntry(entry.key[:MAX_CONTEXT_KEY], entry.value[:MAX_CONTEXT_VALUE])
                    for entry in entries[:MAX_CONTEXT]
                )
            except Exception as exc:
                raise DialogueError("Memory retrieval failed") from exc
        result = None
        if decision.tool_call is not None:
            call = decision.tool_call
            try:
                value = self.tools.execute(call.name, call.arguments)
                if not isinstance(value, str):
                    raise TypeError("Tool must return text")
                result = ToolResult(call.name, value[:MAX_TOOL_RESULT])
            except ToolDenied:
                raise
            except Exception as exc:
                raise DialogueError("Tool execution failed; it may have produced side effects. Do not retry automatically.") from exc
        request = ProviderRequest(message, decision.intent, context, history, result)
        try:
            reply = self.provider.generate(request)
            if not isinstance(reply, str) or not reply.strip():
                raise TypeError("Provider must return nonempty text")
        except Exception as exc:
            detail = "Provider failed after tool execution; do not retry automatically" if result else "Provider failed"
            raise DialogueError(detail) from exc
        return asdict(DialogueResponse(reply, decision.intent.value, tuple(entry.key for entry in context), result.name if result else None))

    @staticmethod
    def _text(value):
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_MESSAGE:
            raise ValueError("Message must contain 1..4000 characters and cannot be blank")
        return value.strip()

    @classmethod
    def _history(cls, value):
        if not isinstance(value, list) or len(value) > MAX_HISTORY:
            raise ValueError("history must be a list of at most 20 messages")
        history = []
        for item in value:
            if not isinstance(item, dict) or set(item) != {"role", "content"}:
                raise ValueError("History messages require role and content")
            if item["role"] not in ("user", "assistant"):
                raise ValueError("History role must be user or assistant")
            history.append(Message(item["role"], cls._text(item["content"])))
        return tuple(history)
