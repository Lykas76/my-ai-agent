"""Public errors contain fixed messages, never upstream bodies."""
class PublicError(ValueError):
    def __init__(self, code, message, status=400):
        self.code, self.status = code, status
        super().__init__(message)


class ProviderError(PublicError):
    def __init__(self):
        super().__init__("provider_unavailable", "Provider unavailable", 503)


class ToolFailure(PublicError):
    def __init__(self, timeout=False):
        super().__init__("tool_timeout" if timeout else "tool_failed",
                         "Tool execution failed; actions may have completed. Do not retry automatically.", 503)
