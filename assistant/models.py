from dataclasses import dataclass, field


@dataclass(frozen=True)
class Request:
    action: str
    parameters: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict) or set(payload) - {"action", "parameters", "user_id", "session_id"}:
            raise ValueError("Expected action and optional parameters")
        action = payload.get("action")
        parameters = payload.get("parameters", {})
        if not isinstance(action, str) or not action or not isinstance(parameters, dict):
            raise ValueError("action must be a nonempty string; parameters must be an object")
        parameters = dict(parameters)
        for name in ("user_id", "session_id"):
            if name in payload:
                if name in parameters:
                    raise ValueError(f"Duplicate {name}")
                parameters[name] = payload[name]
        if action not in ("dialogue", "tool.run") and any(name in parameters for name in ("user_id", "session_id")):
            raise ValueError("Session identifiers are supported only for dialogue and tool.run")
        return cls(action, parameters)


@dataclass(frozen=True)
class Response:
    ok: bool
    result: object = None
    error: str | None = None
