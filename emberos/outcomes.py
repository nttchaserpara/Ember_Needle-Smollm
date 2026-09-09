"""Explicit tool outcomes that remain compatible with existing text callers."""


class ToolOutput(str):
    """Text plus execution metadata, assigned where the outcome is known.

    No status is inferred by searching the returned text. A plain string is
    still accepted by the registry for legacy tools, with status 'returned'.
    """

    def __new__(cls, text, *, status="success", message="", data=None):
        if status not in {"success", "fallback", "error", "unsupported", "partial"}:
            raise ValueError(f"Invalid tool output status: {status}")
        obj = super().__new__(cls, text)
        obj.status = status
        obj.success = status in {"success", "fallback"}
        obj.message = message
        obj.data = data
        return obj

    @classmethod
    def failure(cls, text, *, status="error", data=None):
        return cls(text, status=status, data=data)
