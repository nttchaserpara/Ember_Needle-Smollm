"""Natural English presentation based only on reported tool outcomes."""

import json


def format_tool_response(result):
    status = result.get("status", "returned" if result["success"] else "error")
    if not result["success"]:
        prefix = "[PARTIAL]" if status == "partial" else "[UNAVAILABLE]" if status == "unsupported" else "[FAILED]"
        return f"{prefix} {result.get('error') or result.get('result') or 'The request could not be completed.'}"
    value = result.get("result")
    message = result.get("message", "")
    prefix = "[FALLBACK]" if status == "fallback" else "[OK]" if status == "success" else "[RESULT]"
    if status == "success" and message and result.get("tool") in {"set_volume", "set_brightness"}:
        return f"{prefix} {message}"
    parts = [f"{prefix} {message}"] if message else [prefix]
    if value is not None and value != "":
        body = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)
        if not message:
            return f"{prefix} {body}"
        parts.append(body)
    return "\n".join(parts)
