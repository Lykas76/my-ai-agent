from assistant.history import HistoryPolicy
from security.confirmations import ConfirmationRejected
from memory.sessions import SessionStore
from assistant.dialogue import DialogueHandler
from assistant.providers import AIProvider
from assistant.models import Request, Response
from memory.base import Memory
from tools.registry import ToolRegistry, ConfirmationRequired
from security.store import AccessContext, AuthenticationError, IdentityError, User


class Assistant:
    def __init__(self, memory: Memory, tools: ToolRegistry, provider: AIProvider | None = None,
                 *, sessions: SessionStore | None = None, history_policy: HistoryPolicy | None = None,
                 access: AccessContext | None = None):
        self.memory = memory
        self.tools = tools
        self.access = access
        self.security = getattr(memory, "security", None)
        self.dialogue = DialogueHandler(memory, tools, provider, sessions=sessions, history_policy=history_policy, access=access)

    def for_user(self, user: User):
        # HTTP constructs a fresh binding per request, never mutates shared identity.
        if self.security is None or not hasattr(self.memory, "for_user"):
            raise AuthenticationError()
        return Assistant(self.memory.for_user(user.user_id), self.tools, self.dialogue.provider,
                         sessions=self.dialogue.sessions, history_policy=self.dialogue.history_policy,
                         access=AccessContext(user.user_id, self.security))

    def handle(self, request: Request) -> Response:
        try:
            request = Request.from_dict({"action": request.action, "parameters": request.parameters})
            p = request.parameters
            if self.access is not None:
                user = self.access.store.get_user(self.access.user_id)
                if user is None or user.status != "active":
                    raise AuthenticationError()
                if "user_id" in p and p["user_id"] != self.access.user_id:
                    raise IdentityError()
                if request.action == "dialogue":
                    p = {**p, "user_id": self.access.user_id}
            if request.action in ("confirmation.get", "confirmation.approve", "confirmation.cancel"):
                if self.access is None:
                    raise AuthenticationError()
                if set(p) != {"confirmation_id"}:
                    raise ValueError("Expected confirmation_id only")
                operation = request.action.split(".")[1]
                result = self.tools._confirm(self.access, operation, self.access.user_id, p["confirmation_id"])
                return Response(True, result)
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
                if "confirmation_id" in p and not isinstance(p["confirmation_id"], str):
                    raise ConfirmationRejected()
                return Response(True, self.tools.execute(
                    name, arguments, access=self.access, confirmation_id=p.get("confirmation_id"),
                    session_id=p.get("session_id"),
                ))
            return Response(False, error="Unknown action")
        except ConfirmationRequired as exc:
            confirmation = exc.confirmation
            return Response(True, {"status": "confirmation_required", "tool": exc.tool, "permission": "sensitive",
                                   "confirmation_id": confirmation["confirmation_id"], "session_id": confirmation["session_id"],
                                   "expires_at": confirmation["expires_at"]})
        except ValueError as exc:
            return Response(False, error=str(exc))

    @staticmethod
    def _text(parameters, name):
        value = parameters.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonempty string")
        return value
