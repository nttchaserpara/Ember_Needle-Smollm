"""Verify explicit volume levels survive low-confidence routing safely."""

from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import needle_router
from emberos.tools import ToolRegistry, ToolResult


def run_case(proposal, expected_route, expected_calls, query="Change the volume to 25%."):
    registry = ToolRegistry(memory=object())
    calls = []

    def record(name, arguments):
        calls.append({"name": name, "arguments": arguments})
        return ToolResult(True, result="Evaluation only; no action performed.")

    model_result = {
        "success": True,
        "error": None,
        "confidence": 0.1764,
        "reasoning": None,
        "function_calls": [proposal],
        "validation": {"ungrounded": [], "negation": False},
    }
    with patch("emberos.history_routing.select_history", return_value={"function_calls": []}), \
         patch.object(registry, "execute_tool", side_effect=record), \
         patch.object(needle_router.needle_agent, "complete", return_value=model_result):
        response = needle_router.route_and_execute(query, registry)

    assert response["route"] == expected_route, response
    assert calls == expected_calls, calls
    return response


def main():
    expected = [{"name": "set_volume", "arguments": {"level": 25}}]
    response = run_case(
        {"name": "set_volume", "arguments": {"level": 25}}, "tool_call", expected
    )
    assert response["tool"] == "set_volume", response
    print("PASS low-confidence explicit set_volume uses the literal percentage")

    response = run_case(
        {"name": "volume_down", "arguments": {"steps": 2}}, "tool_call", expected
    )
    assert response["tool"] == "set_volume", response
    print("PASS related low-confidence volume proposal is corrected to absolute level")

    response = run_case(
        {"name": "get_volume", "arguments": {}},
        "unresolved_tool_request",
        [],
    )
    assert response["reason"] == "low_confidence", response
    print("PASS low-confidence conflicting tool proposal is still rejected")

    response = run_case(
        {"name": "set_volume", "arguments": {"level": 25}},
        "unresolved_tool_request",
        [],
        query="Change the volume to 25% and set brightness to 30%.",
    )
    assert response["reason"] == "low_confidence", response
    print("PASS compound requests do not use single-action volume grounding")


if __name__ == "__main__":
    main()
