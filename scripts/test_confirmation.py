"""Standalone checks for the single-user destructive-action confirmation gate."""

from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import run_ember
from emberos.tools import ToolRegistry
from web_ember import WebSafeToolRegistry


class StubRenderer:
    def render(self, result, **kwargs):
        text = result.get("display_response") or result.get("response") or ""
        return {
            "text": text,
            "original": text,
            "candidate": "",
            "source": "original",
            "reason": "test",
            "elapsed_seconds": 0,
            "generation": None,
            "history_turn_ids": [],
        }


def test_confirmation_gate():
    for registry_type in (ToolRegistry, WebSafeToolRegistry):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "victim.txt"
            target.write_text("safe", encoding="utf-8")
            registry = registry_type()

            def fake_route(query, gated_registry, **kwargs):
                execution = gated_registry.execute_tool(
                    "delete_file", {"path": str(target)}
                )
                return {
                    "route": "tool_call",
                    "tool": "delete_file",
                    "arguments": {"path": str(target)},
                    "success": execution.success,
                    "result": execution.result,
                    "error": execution.error,
                    "status": execution.status,
                    "message": execution.message,
                    "data": execution.data,
                }

            with patch.object(run_ember, "route_and_execute", side_effect=fake_route):
                first = run_ember.handle_request(
                    "delete the file", registry, reply_renderer=StubRenderer()
                )
                assert first["route"] == "confirmation_required", first
                assert target.exists(), "destructive action ran before confirmation"

                cancelled = run_ember.handle_request(
                    "okay", registry, reply_renderer=StubRenderer()
                )
                assert cancelled["route"] == "no_action", cancelled
                assert target.exists(), "non-confirmation should cancel the action"

                second = run_ember.handle_request(
                    "delete the file", registry, reply_renderer=StubRenderer()
                )
                assert second["route"] == "confirmation_required", second

                confirmed = run_ember.handle_request(
                    "YES", registry, reply_renderer=StubRenderer()
                )
                assert confirmed["route"] == "tool_call", confirmed
                assert not target.exists(), "exact confirmation did not execute action"


if __name__ == "__main__":
    test_confirmation_gate()
    print("PASS: confirmation gate and exact-word cancellation")
