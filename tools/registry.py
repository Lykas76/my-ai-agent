from dataclasses import dataclass
from typing import Callable


class ToolDenied(ValueError):
    pass


@dataclass(frozen=True)
class Tool:
    name: str
    handler: Callable[[dict], str]
    description: str = ""


class ToolRegistry:
    """Only trusted application code may register and enable handlers.

    This is an allowlist, not a sandbox for untrusted Python code.
    Request payloads cannot grant permissions.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._enabled: set[str] = set()

    def register(self, tool: Tool, *, enabled: bool = False) -> None:
        if not tool.name or tool.name in self._tools:
            raise ValueError("Tool name must be nonempty and unique")
        self._tools[tool.name] = tool
        if enabled:
            self._enabled.add(tool.name)

    def execute(self, name: str, arguments: dict) -> str:
        if name not in self._enabled:
            raise ToolDenied("Tool is unknown or disabled")
        return self._tools[name].handler(dict(arguments))
