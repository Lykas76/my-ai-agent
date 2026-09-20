from dataclasses import dataclass
from typing import Callable

from security.store import AccessContext, PERMISSIONS, validate_tool_name


class ToolDenied(ValueError):
    pass


class ConfirmationRequired(ValueError):
    def __init__(self, tool: str):
        self.tool = tool
        super().__init__("Confirmation required")


@dataclass(frozen=True)
class Tool:
    name: str
    handler: Callable[[dict], str]
    description: str = ""
    permission: str | None = None


class ToolRegistry:
    """Default deny; registration, enablement and exact user grant are all required."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._enabled: set[str] = set()

    def register(self, tool: Tool, *, enabled: bool = False) -> None:
        validate_tool_name(tool.name)
        if tool.name in self._tools:
            raise ValueError("Tool name must be unique")
        self._tools[tool.name] = tool
        if enabled:
            self._enabled.add(tool.name)

    def execute(self, name: str, arguments: dict, *, access: AccessContext | None = None) -> str:
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
            raise ToolDenied("Tool execution denied")
        if decision == "confirmation_required":
            raise ConfirmationRequired(tool.name)
        try:
            return tool.handler(dict(arguments))
        except Exception as exc:
            raise ValueError("Tool execution failed; actions may have completed. Do not retry automatically.") from exc
