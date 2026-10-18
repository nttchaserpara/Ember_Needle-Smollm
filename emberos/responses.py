"""Natural English presentation based only on reported tool outcomes."""

import json
import re


_STATUS_PREFIX = re.compile(r"^\s*\[(?:OK|FAILED|PARTIAL|UNAVAILABLE|RESULT|FALLBACK)\]\s*", re.I)


def _clean_status_label(value):
    return _STATUS_PREFIX.sub("", value, count=1) if isinstance(value, str) else value


def format_tool_response(result):
    status = result.get("status", "returned" if result["success"] else "error")
    if not result["success"]:
        detail = result.get("error") or result.get("result") or "The request could not be completed."
        detail = _clean_status_label(detail)
        if status == "partial":
            return str(detail)
        if status == "unsupported":
            return f"This feature isn't available here. {detail}"
        return str(detail)
    value = result.get("result")
    message = result.get("message", "")
    if result.get("tool") == "get_agent_context" and isinstance(value, dict):
        host = value.get("host_name") or "the backend host"
        system = value.get("operating_system") or "an unknown operating system"
        model = value.get("device_model")
        device = f" ({model})" if model and model != "not detected" else ""
        surface = value.get("session_mode") or "unknown"
        runtime_surface = value.get("runtime_surface")
        through = f" Requests come through {runtime_surface}." if runtime_surface else ""
        return f"EmberOS is running on the backend host {host}{device}, which uses {system} in a {surface} session.{through}"
    if result.get("tool") == "get_agent_capabilities" and isinstance(value, dict):
        areas = ", ".join(value.get("capability_areas") or [])
        if not areas:
            return "No tool capabilities are currently registered."
        return (
            f"I can help with {areas}. Individual tools may depend on the platform, "
            "hardware, and installed dependencies."
        )
    if result.get("tool") == "get_system_info" and isinstance(value, dict):
        system = value.get("os") or "the current operating system"
        architecture = value.get("cpu_arch")
        cores = value.get("cpu_cores")
        ram = value.get("ram_gb")
        details = [f"{system} system"]
        if architecture:
            details[0] += f" ({architecture})"
        if cores is not None:
            details.append(f"{cores} CPU cores")
        if ram is not None:
            details.append(f"{ram} GB of installed RAM")
        if value.get("gpu_mode") == "not_probed":
            details.append("GPU not probed")
        return "This device has " + ", ".join(details) + "."
    if message:
        return _clean_status_label(message)
    if value is not None and value != "":
        body = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)
        return _clean_status_label(body)
    if status == "success":
        return "Done."
    return "The request finished without a result to show."
