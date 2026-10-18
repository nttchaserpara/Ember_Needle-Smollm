"""Check partial filename clarification and selection without file actions."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import needle_router
import run_ember
from emberos.replies import original_response
from emberos.tools import ToolRegistry, ToolResult


class _OriginalTextRenderer:
    def render(self, result, **_kwargs):
        text = original_response(result)
        return {"text": text, "original": text, "candidate": "", "source": "original"}


def main():
    with TemporaryDirectory(prefix="ember-file-reference-") as temporary:
        root = Path(temporary)
        first = root / "emberos-smoke-robot-report.txt"
        second = root / "emberos-smoke-robot-report-copy.txt"
        first.write_text("Disposable file A.", encoding="utf-8")
        second.write_text("Disposable file B.", encoding="utf-8")
        candidates = [str(first), str(second)]
        registry = ToolRegistry()

        def resolve_reference(reference):
            if reference == "robot report":
                return candidates
            return [str(Path(reference))] if Path(reference).is_file() else []

        envelope = {
            "success": True,
            "function_calls": [{"name": "read_file", "arguments": {"path": "report"}}],
            "confidence": 0.0,
            "validation": {"ungrounded": ["read_file.path"], "negation": False},
        }
        session_id = "partial-file-reference-test"
        with patch("emberos.history_routing.select_history", return_value={"function_calls": []}), \
             patch.object(needle_router.needle_agent, "complete", return_value=envelope), \
             patch.object(run_ember, "_resolve_file_reference", side_effect=resolve_reference), \
             patch.object(registry, "execute_tool", return_value=ToolResult(True, result="Disposable file A.")) as execute:
            clarification = run_ember.handle_request(
                "Read file robot report", registry,
                reply_renderer=_OriginalTextRenderer(), session_id=session_id,
            )
            assert clarification["route"] == "file_clarification", clarification
            assert "Which one should I read?" in clarification["display_response"]
            assert first.name in clarification["display_response"]
            assert second.name in clarification["display_response"]
            execute.assert_not_called()

            selected = run_ember.handle_request(
                first.name, registry, reply_renderer=_OriginalTextRenderer(),
                session_id=session_id,
            )
            assert selected["success"] is True, selected
            execute.assert_called_once_with("read_file", {"path": str(first)})

            follow_up = run_ember.handle_request(
                "delete it", registry, reply_renderer=_OriginalTextRenderer(),
                session_id=session_id,
            )
            assert follow_up["route"] == "confirmation_required", follow_up
            assert str(first) in follow_up["display_response"], follow_up
            assert first.exists(), "a delete follow-up must wait for explicit confirmation"
            execute.assert_called_once_with("read_file", {"path": str(first)})

            cancelled = run_ember.handle_request(
                "no", registry, reply_renderer=_OriginalTextRenderer(),
                session_id=session_id,
            )
            assert cancelled["status"] == "cancelled", cancelled
            assert first.exists(), "declining confirmation must preserve the file"

            with patch.object(run_ember, "route_and_execute", return_value={
                "route": "no_action", "success": False, "response": "No action."
            }):
                run_ember.handle_request(
                    "hello", registry, reply_renderer=_OriginalTextRenderer(),
                    session_id=session_id,
                )
            assert run_ember._get_recent_file_context(session_id) is None
            stale_follow_up = run_ember.handle_request(
                "delete it", registry, reply_renderer=_OriginalTextRenderer(),
                session_id=session_id,
            )
            assert stale_follow_up["route"] == "no_action", stale_follow_up
            assert "Read or select a file first" in stale_follow_up["display_response"]
            assert first.exists()

            no_context = run_ember.handle_request(
                "delete it", registry, reply_renderer=_OriginalTextRenderer(),
                session_id="no-file-context-test",
            )
            assert no_context["route"] == "no_action", no_context
            assert "Read or select a file first" in no_context["display_response"]
            assert first.exists()

    assert needle_router._known_file_reference_call("delete it") is None
    print("PASS ambiguous read selection; immediate delete-it confirms exact file; no/stale context is safe")


if __name__ == "__main__":
    main()
