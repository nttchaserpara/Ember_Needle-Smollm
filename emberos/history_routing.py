"""A small Needle view for distinguishing conversation recall from live data.

Uses the same native engine/weights as the main router. The contrast tools are
selection candidates only: this stage guards against conflicting live actions.
The main router supplies executable history calls and their arguments. No examples, phrase rules, or saved conversation text enter its input.
"""

import re

import needle


_DISCUSSION = r"(?:discuss(?: about)?|talk about)"
_SHOW = r"(?:(?:could|can|would) you )?(?:please )?(?:show|list|find)(?: me)? "
_MESSAGES = r"(?:my |our |the )?(?:recent )?(?:conversations|chats|messages)"
_HISTORY_REQUEST = re.compile(
    rf"(?:what did we {_DISCUSSION}\b|(?:which|what) .+? did we {_DISCUSSION}\b|{_SHOW}{_MESSAGES}\b)",
    re.I,
)
_SCOPES = {
    "last time": "previous_session", "in the previous session": "previous_session",
    "in the last session": "previous_session", "in this session": "current_session",
}


def explicit_history_arguments(query):
    """Return grounded slots, None for another intent, or raise for unclear history requests."""
    text = " ".join(query.split()).rstrip("?.!")
    positive = re.sub(r"^(?:please )?(?:don't|don’t|do not|never) ", "", text, flags=re.I)
    if not _HISTORY_REQUEST.match(positive):
        return None
    if positive != text:
        raise ValueError("negated_request")
    scope = "all"
    for suffix, value in _SCOPES.items():
        if text.lower().endswith(" " + suffix):
            text = text[:-(len(suffix) + 1)]
            scope = value
            break
    # A topic is a short phrase supplied by the user. Compound requests and
    # unparsed time restrictions need clarification before accessing history.
    if re.search(r"\b(and|then|or|but|not|never|except|only|session|yesterday|today|last|since|before|after|from)\b", text, re.I):
        raise ValueError("uncertain_history_request")
    topic = r"(?P<topic>[\w-]+(?: [\w-]+){0,7})"
    if re.fullmatch(r"what did we (?:discuss|talk about)", text, re.I):
        return {"query": "", "scope": scope}
    if re.fullmatch(r"what did we discuss about", text, re.I):
        raise ValueError("uncertain_history_request")
    patterns = (
        rf"what did we {_DISCUSSION} (?:my |the )?{topic}",
        rf"(?:which|what) {topic} did we {_DISCUSSION}",
        rf"{_SHOW}{_MESSAGES}(?: about (?:my |the )?{topic})?",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, text, flags=re.I)
        if match:
            return {"query": match["topic"] or "", "scope": scope}
    raise ValueError("uncertain_history_request")


HISTORY_SCHEMAS = [
    {
        "name": "search_conversation_history",
        "description": "Search conversation memory for past messages, discussions and remembered information. List saved conversations when no topic is specified.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_system_info",
        "description": "Read hardware specifications, CPU and total physical RAM capacity in gigabytes.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "disk_usage",
        "description": "Measure disk usage now. Only for requests about current storage; cannot answer what was said in previous conversations.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "search_notes",
        "description": "Search saved notebook entries by title, content or tag. Does not search conversations.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "list_tasks",
        "description": "List current pending or completed to-do tasks. Does not retrieve previous conversations.",
        "parameters": {"type": "object", "properties": {}},
    },
]

_selector = None


def select_history(query):
    global _selector
    if _selector is None:
        _selector = needle.Needle(tools=HISTORY_SCHEMAS)
    _selector.reset()
    return _selector.complete(query)
