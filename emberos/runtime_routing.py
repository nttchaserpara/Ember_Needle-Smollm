"""Narrow Needle selectors for facts about the active EmberOS backend.

These read-only fact selectors stay separate from the large action catalogue
and the conversation-history selector. Their outputs are only proposals;
route_and_execute validates the selected tool and applies a high confidence
threshold before reading any facts.
"""

import needle


@needle.tool
def get_agent_capabilities():
    """Answer questions about what this assistant can do or help with. Return
    the available capabilities and tools registered by the current EmberOS
    installation. Not for computer, OS, CPU, RAM, or volume questions.
    """
    pass


@needle.tool
def get_agent_context():
    """Identify the computer and operating system hosting the EmberOS backend.
    Use for questions asking where EmberOS is running, what computer this is
    running on, or what OS the backend runs. Do not use for application
    windows, hardware specifications, assistant capabilities, or conversation
    history.
    """
    pass


@needle.tool
def cpu_temperature():
    """Measure the current CPU or processor temperature. Use when asked about
    CPU heat, how hot this computer or Raspberry Pi is, or whether it is
    overheating. Do not use for greetings or thank-you messages.
    """
    pass


_capability_selector = None
_context_selector = None
_cpu_temperature_selector = None

CAPABILITY_CONFIDENCE_THRESHOLD = 0.97
CONTEXT_CONFIDENCE_THRESHOLD = 0.90
CPU_TEMPERATURE_CONFIDENCE_THRESHOLD = 0.95


def _valid_selection(result, tool_name, threshold):
    if not isinstance(result, dict) or result.get("success") is not True or result.get("error"):
        return False
    validation = result.get("validation") or {}
    if validation.get("negation") or validation.get("ungrounded"):
        return False
    calls = result.get("function_calls") or []
    confidence = result.get("confidence")
    return (
        len(calls) == 1
        and calls[0].get("name") == tool_name
        and calls[0].get("arguments") == {}
        and confidence is not None
        and confidence >= threshold
    )


def select_runtime_fact(query):
    """Return a strongly selected context/capabilities tool, or no selection.

    Capability selection runs first because it answers an explicit question
    about EmberOS features. The context selector runs only if that did not
    match. No request text or prior conversation is retained between calls.
    """
    global _capability_selector, _context_selector
    attempts = {}
    if _capability_selector is None:
        _capability_selector = needle.Needle(tools=[get_agent_capabilities])
    _capability_selector.reset()
    capability_result = _capability_selector.complete(query)
    attempts["capabilities"] = capability_result
    if _valid_selection(
            capability_result, "get_agent_capabilities", CAPABILITY_CONFIDENCE_THRESHOLD):
        return {
            "tool": "get_agent_capabilities",
            "confidence": capability_result["confidence"],
            "selection": capability_result,
            "attempts": attempts,
        }

    if _context_selector is None:
        _context_selector = needle.Needle(tools=[get_agent_context])
    _context_selector.reset()
    context_result = _context_selector.complete(query)
    attempts["context"] = context_result
    if _valid_selection(
            context_result, "get_agent_context", CONTEXT_CONFIDENCE_THRESHOLD):
        return {
            "tool": "get_agent_context",
            "confidence": context_result["confidence"],
            "selection": context_result,
            "attempts": attempts,
        }
    return {"tool": None, "selection": None, "attempts": attempts}


def confirm_cpu_temperature_request(query):
    """Require a clear CPU-temperature intent before a stray proposal runs."""
    global _cpu_temperature_selector
    if _cpu_temperature_selector is None:
        _cpu_temperature_selector = needle.Needle(tools=[cpu_temperature])
    _cpu_temperature_selector.reset()
    result = _cpu_temperature_selector.complete(query)
    return {
        "selected": _valid_selection(
            result, "cpu_temperature", CPU_TEMPERATURE_CONFIDENCE_THRESHOLD
        ),
        "result": result,
    }
