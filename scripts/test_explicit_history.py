"""Exercise explicit recall, session scope and abstention through both registries."""

from pathlib import Path
import json
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
    fixture = json.loads((Path(__file__).parent / "fixtures/history_language_en.json").read_text())
    for case in fixture["cases"]:
        if case["type"] == "other":
            assert explicit_history_arguments(case["query"]) is None, case["query"]

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
                history_cases = [case for case in fixture["cases"] if case["type"] == "history"]
                for case in history_cases:
                    result = route_and_execute(case["query"], registry)
                    assert result["tool"] == "search_conversation_history", result
                    assert result["arguments"] == case["expected"], result

                for query, scope, answer, excluded in (
                    ("Which budgets did we discuss last time?", "previous_session", "Old plan", "New plan"),
                    ("Can you list our chats about budgets in this session?", "current_session", "New plan", "Old plan"),
                ):
                    result = route_and_execute(query, registry)
                    assert result["arguments"] == {"scope": scope, "query": "budgets"}, result
                    assert answer in result["result"] and excluded not in result["result"], result

                with patch.object(registry, "execute_tool", side_effect=AssertionError):
                    for case in fixture["cases"]:
                        if case["type"] != "abstain":
                            continue
                        result = route_and_execute(case["query"], registry)
                        assert result["reason"] == case["expected_reason"], result
                    registry.memory = None
                    result = route_and_execute("What did we discuss about RAM?", registry)
                    assert result["reason"] == "memory_unavailable", result

    print("PASS explicit history scope, abstention, disabled memory and live-query boundaries")


if __name__ == "__main__":
    main()
