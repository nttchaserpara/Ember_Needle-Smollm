"""A small Needle view for distinguishing conversation recall from live data.

Uses the same native engine/weights as the main router. The contrast tools are
selection candidates only: this stage guards against conflicting live actions.
The main router supplies executable history calls and their arguments. No examples, phrase rules, or saved conversation text enter its input.
"""

import needle


HISTORY_SCHEMAS = [
    {
        "name": "search_conversation_history",
        "description": "Retrieve past conversations between the user and assistant, including previous requests, answers, discussions and reported settings. Searches saved messages relevant to the current question.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_system_info",
        "description": "Read current CPU architecture, core counts and installed memory. Cannot retrieve previous conversations.",
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
