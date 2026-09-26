from dataclasses import dataclass
from typing import Callable
import json
import time
from tools.schema import validate
from runtime.errors import ToolFailure
from security.confirmations import ConfirmationRejected, DEFAULT_TTL, canonical_arguments, validate_schema, validate_ttl

from security.store import AccessContext, PERMISSIONS, validate_tool_name


class ToolDenied(ValueError):
    pass


class ConfirmationRequired(ValueError):
    def __init__(self, confirmation: dict):
        self.confirmation = confirmation
        self.tool = confirmation["tool"]
        super().__init__("Confirmation required")


@dataclass(frozen=True)
class ToolResult:
    name: str
    value: str
    status: str = "success"


@dataclass(frozen=True)
class ToolContext:
    access: AccessContext
    deadline: float

    def remaining(self):
        seconds = self.deadline - time.monotonic()
        if seconds <= 0:
            raise ToolFailure(timeout=True)
        return seconds


@dataclass(frozen=True)
class Tool:
    name: str
    handler: Callable[[dict], str]
    description: str = ""
    permission: str | None = None
    confirmation_schema: dict | None = None
    schema: dict | None = None
    timeout_seconds: float = 10.0
    contextual: bool = False
    retry_safe: bool = False
    expose_to_model: bool = True


class ToolRegistry:
    """Default deny; registration, enablement and exact user grant are all required."""

    def __init__(self, *, confirmation_ttl_seconds: int = DEFAULT_TTL):
        self.confirmation_ttl_seconds = validate_ttl(confirmation_ttl_seconds)
        self._tools: dict[str, Tool] = {}
        self._enabled: set[str] = set()

    def register(self, tool: Tool, *, enabled: bool = False) -> None:
        validate_tool_name(tool.name)
        if not 0 < tool.timeout_seconds <= 60:
            raise ValueError("Invalid tool timeout")
        if tool.name in self._tools:
            raise ValueError("Tool name must be unique")
        self._tools[tool.name] = tool
        if enabled:
            self._enabled.add(tool.name)

    def execute(self, name: str, arguments: dict, *, access: AccessContext | None = None,
                confirmation_id: str | None = None, session_id: str | None = None, new_session: bool = False) -> str:
        tool = self._tools.get(name)
        permission = tool.permission if tool and isinstance(tool.permission, str) and tool.permission in PERMISSIONS else None
        decision = "deny"
        if access is None:
            raise ToolDenied("Tool execution denied")
        try:
            if tool and name in self._enabled and access.store.is_allowed(access.user_id, name, permission):
                decision = "confirmation_required" if permission == "sensitive" else "allow"
            # Unknown names are untrusted text: never persist them or tool arguments.
            access.store.audit(access.user_id, tool.name if tool else "<unknown>", permission, decision)
        except Exception as exc:
            raise ToolDenied("Tool execution denied") from exc
        if decision == "deny":
            if confirmation_id is not None:
                self._confirm(access, "reject", access.user_id, confirmation_id)
            raise ToolDenied("Tool execution denied")
        if not isinstance(arguments, dict):
            raise ValueError("Invalid tool arguments")
        if tool.schema is not None:
            validate(arguments, tool.schema)
        if session_id is not None and permission != "sensitive" and confirmation_id is None:
            from memory.sessions import validate_id
            validate_id(session_id, "session_id")
            row = access.store._connection.execute(
                "SELECT 1 FROM sessions WHERE user_id=? AND session_id=?", (access.user_id, session_id)
            ).fetchone()
            if row is None:
                raise ToolDenied("Tool execution denied")
        if permission == "sensitive" or confirmation_id is not None:
            try:
                canonical, _ = canonical_arguments(arguments)
                frozen_arguments = json.loads(canonical)
                if permission == "sensitive":
                    validate_schema(frozen_arguments, tool.confirmation_schema)
            except ValueError:
                self._confirm(access, "reject", access.user_id, confirmation_id)
            if confirmation_id is not None:
                consumed = self._confirm(access, "consume", access.user_id, confirmation_id,
                                         name, permission, frozen_arguments, session_id)
                # Execute exactly the persisted arguments whose digest was approved.
                arguments = consumed["arguments"]
            elif decision == "confirmation_required":
                confirmation = self._confirm(access, "create", access.user_id, name, permission, frozen_arguments,
                                             session_id=session_id, ttl=self.confirmation_ttl_seconds, new_session=new_session)
                raise ConfirmationRequired(confirmation)
        try:
            context = ToolContext(access, time.monotonic() + tool.timeout_seconds)
            value = tool.handler(dict(arguments), context) if tool.contextual else tool.handler(dict(arguments))
            context.remaining()
            if isinstance(value, ToolResult):
                value = value.value
            if not isinstance(value, str):
                raise ToolFailure()
            return value
        except Exception as exc:
            raise ValueError("Tool execution failed; actions may have completed. Do not retry automatically.") from exc

    def execute_result(self, name, arguments, **kwargs):
        return ToolResult(name, self.execute(name, arguments, **kwargs))

    def definitions(self, access):
        if access is None:
            return ()
        return tuple({"type": "function", "function": {
            "name": tool.name, "description": tool.description, "parameters": tool.schema}}
            for tool in self._tools.values() if tool.name in self._enabled and tool.schema is not None
            and tool.expose_to_model
            and access.store.is_allowed(access.user_id, tool.name, tool.permission))

    @staticmethod
    def _confirm(access, operation, *args, **kwargs):
        try:
            return getattr(access.store.confirmations, operation)(*args, **kwargs)
        except ConfirmationRejected:
            raise
        except Exception as exc:
            raise ConfirmationRejected() from exc
