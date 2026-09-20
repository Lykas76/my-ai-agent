from assistant.history import HistoryPolicy
from memory.sessions import SessionStore
from assistant.dialogue import DialogueHandler
from assistant.providers import AIProvider
from assistant.models import Request, Response
from memory.base import Memory
from tools.registry import ToolRegistry


class Assistant:
    def __init__(self, memory: Memory, tools: ToolRegistry, provider: AIProvider | None = None,
                 *, sessions: SessionStore | None = None, history_policy: HistoryPolicy | None = None):
        self.memory = memory
        self.tools = tools
        self.dialogue = DialogueHandler(memory, tools, provider, sessions=sessions, history_policy=history_policy)

    def handle(self, request: Request) -> Response:
        try:
            request = Request.from_dict({"action": request.action, "parameters": request.parameters})
            p = request.parameters
            if request.action == "dialogue":
                return Response(True, self.dialogue.handle(p))
            if request.action == "ping":
                return Response(True, "pong")
            if request.action in {"memory.put", "memory.get", "memory.delete"}:
                key = self._text(p, "key")
                if request.action == "memory.put":
                    self.memory.put(key, self._text(p, "value"))
                    return Response(True, "saved")
                if request.action == "memory.get":
                    return Response(True, self.memory.get(key))
                return Response(True, self.memory.delete(key))
            if request.action == "tool.run":
                name = self._text(p, "name")
                arguments = p.get("arguments", {})
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be an object")
                return Response(True, self.tools.execute(name, arguments))
            return Response(False, error="Unknown action")
        except ValueError as exc:
            return Response(False, error=str(exc))

    @staticmethod
    def _text(parameters, name):
        value = parameters.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonempty string")
        return value
