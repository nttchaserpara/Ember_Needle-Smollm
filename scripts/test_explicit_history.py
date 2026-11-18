"""Exercise explicit recall, session scope and abstention through both registries."""

from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from emberos.history_routing import explicit_history_arguments
from emberos.memory import ConversationMemory
from emberos.tools import ToolRegistry
from needle_router import route_and_execute
from web_ember import WebSafeToolRegistry


def main():
    for query in ("disk space", "How much free disk space do I have?", "List my pending tasks"):
        assert explicit_history_arguments(query) is None, query

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "history.sqlite3"
        previous = ConversationMemory(path)
        previous.record("Review budgets", "Old plan", {"status": "success"})
        current = ConversationMemory(path)
        current.record("Review budgets", "New plan", {"status": "success"})

        for registry_type in (ToolRegistry, WebSafeToolRegistry):
            registry = registry_type(memory=current)
            with patch("emberos.history_routing.select_history", side_effect=AssertionError), \
                 patch("needle_router.needle_agent.complete", side_effect=AssertionError):
                for query, scope, answer, excluded in (
                    ("Which budgets did we discuss last time?", "previous_session", "Old plan", "New plan"),
                    ("Can you list our chats about budgets in this session?", "current_session", "New plan", "Old plan"),
                ):
                    result = route_and_execute(query, registry)
                    assert result["tool"] == "search_conversation_history", result
                    assert result["arguments"] == {"scope": scope, "query": "budgets"}, result
                    assert answer in result["result"] and excluded not in result["result"], result

                with patch.object(registry, "execute_tool", side_effect=AssertionError):
                    for query, reason in (
                        ("Don't show my recent conversations.", "negated_request"),
                        ("What did we discuss about budgets and then check disk space?", "uncertain_history_request"),
                        ("Show our conversations from yesterday", "uncertain_history_request"),
                        ("What did we discuss about?", "uncertain_history_request"),
                    ):
                        assert route_and_execute(query, registry)["reason"] == reason, query
                    registry.memory = None
                    assert route_and_execute("What did we discuss about RAM?", registry)["reason"] == "memory_unavailable"

    print("PASS explicit history scope, abstention, disabled memory and live-query boundaries")


if __name__ == "__main__":
    main()
