"""Exercise the bounded general multi-agent workflow without real OS actions."""

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from contextlib import ExitStack
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class _NoReplyModel:
    def render(self, *args, **kwargs):
        raise AssertionError("verified multi-agent replies must not start a generation worker")


def _context(directory, registry_type, case):
    from emberos.memory import ConversationMemory
    from emberos.tools import ToolResult
    from use_cases.notes import NotesManager

    root = Path(directory)
    note_manager = NotesManager(root / "notes.sqlite3")
    note = note_manager.add("Battery replacement", "Inspect the battery cable before winter.", ["battery"])
    note_manager.export_text(note)

    history_path = root / "history.sqlite3"
    previous = ConversationMemory(history_path)
    previous.record(
        "Review the backup plan",
        "The backup planning note recommends a weekly external copy.",
        {"route": "tool_call", "tool": "search_notes", "arguments": {},
         "success": True, "status": "success"},
    )
    current = ConversationMemory(history_path)
    current.record(
        "We discussed the battery check",
        "The battery should be inspected before winter.",
        {"route": "tool_call", "tool": "search_notes", "arguments": {},
         "success": True, "status": "success"},
    )
    registry = registry_type(memory=current)
    ram = (ToolResult(False, error="RAM reading unavailable", status="unsupported")
           if case.get("live_failure") else
           ToolResult(True, result="Total RAM: 512 MiB; available RAM: 240 MiB."))
    disk = ToolResult(True, result="Current free disk space on /: 16 GiB.")
    fixed = {
        "ram_status": ram,
        "disk_usage": disk,
        "cpu_info": ToolResult(True, result="CPU information: ARM Cortex-A53."),
        "cpu_temperature": ToolResult(True, result="CPU temperature: 43 C."),
        "system_uptime": ToolResult(True, result="System uptime: 2 days."),
        "battery_status": ToolResult(True, result="Battery status: 87%, charging."),
    }
    return registry, current, note_manager, fixed


def _run_case(case, registry_type, directory):
    from needle_router import route_and_execute

    registry, memory, notes, fixed = _context(directory, registry_type, case)
    calls = []
    original_execute = registry.execute_tool

    def record(name, arguments):
        calls.append({"name": name, "arguments": dict(arguments)})
        return original_execute(name, arguments)

    patches = [patch("emberos.tools._get_notes_manager", return_value=notes),
               patch.object(registry, "execute_tool", side_effect=record)]
    patches.extend(patch.object(registry.get_tool(name), "func", return_value=value)
                   for name, value in fixed.items())
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        result = route_and_execute(case["query"], registry)
    actual_tools = [call["name"] for call in calls]
    passed = (result.get("route") == case["expected_route"]
              and result.get("status") == case["expected_status"]
              and actual_tools == case["expected_calls"]
              and all(fragment.casefold() in result.get("response", "").casefold()
                      for fragment in case["response_contains"]))
    if case["expected_source"] is not None:
        retrieval = next((item for item in result.get("trace", []) if item["role"] == "retrieval"), {})
        expected_retrieval_status = case["expected_retrieval_status"]
        passed = (passed and retrieval.get("status") == expected_retrieval_status
                  and retrieval.get("source") == case["expected_source"]
                  and retrieval.get("scope") == case["expected_scope"]
                  and (retrieval.get("source_id") is not None) == (expected_retrieval_status == "found"))
        if case["expected_calls"][0] == "search_conversation_history":
            args = calls[0]["arguments"]
            passed = passed and args.get("scope") == case["expected_scope"]
    if result.get("route") == "multi_agent":
        trace = result.get("trace", [])
        roles = [item["role"] for item in trace]
        expected_roles = (["planner"] if not actual_tools else ["planner", "retrieval"]
                          + (["os_action", "verifier"] if len(actual_tools) == 2 else []))
        linked = all(item.get("parent_task_id") == trace[index - 1].get("task_id")
                     for index, item in enumerate(trace[1:], start=1))
        passed = passed and roles == expected_roles and linked
        if len(actual_tools) == 2:
            source_ids = [item.get("source_id") for item in trace[1:]]
            passed = passed and len(set(source_ids)) == 1
    if case.get("live_failure"):
        passed = passed and result.get("success") is False
    return {"passed": bool(passed), "route": result.get("route"),
            "status": result.get("status"), "calls": calls, "response": result.get("response"),
            "trace": result.get("trace", [])}


def main():
    with TemporaryDirectory(prefix="ember-multi-agent-test-") as root:
        os.environ["EMBER_DATA_DIR"] = root
        from emberos.tools import ToolRegistry
        from needle_router import route_and_execute
        from run_ember import handle_request
        from web_ember import WebSafeToolRegistry
        from emberos.multi_agent import (AgentResult, RetrievedEvidence,
                                         orchestrate_if_requested)
        from emberos.tools import ToolResult

        fixture = json.loads((Path(__file__).parent / "fixtures/multi_agent_general_en.json").read_text())
        registries = (ToolRegistry, WebSafeToolRegistry)
        for registry_type in registries:
            for case in fixture:
                with TemporaryDirectory(prefix="ember-multi-agent-case-") as case_dir:
                    result = _run_case(case, registry_type, case_dir)
                assert result["passed"], (registry_type.__name__, case["query"], result)
                print(f"PASS {registry_type.__name__} {case['query']}")

        with TemporaryDirectory(prefix="ember-multi-agent-boundaries-") as directory:
            registry, _, notes, _ = _context(directory, ToolRegistry, fixture[0])
            mismatch = ToolResult(True, result="untrusted", data={
                "source": "notes", "query": "battery replacement", "scope": "previous_session",
                "status": "matches", "retrieval": "matches", "results": [],
            })
            with patch("emberos.tools._get_notes_manager", return_value=notes), \
                 patch.object(registry, "execute_tool", return_value=mismatch):
                result = orchestrate_if_requested(fixture[0]["query"], registry)
            assert [call["name"] for call in result["calls"]] == ["search_notes"], result
            assert result["trace"][-1]["reason"] == "source_or_scope_mismatch", result
            print("PASS mismatched retrieval scope stops before the live OS read")

            evidence = RetrievedEvidence("notes", 1, "Battery replacement", "2026-01-01",
                                         "Inspect battery cable.", 1.0, 1)
            forged = AgentResult("retrieval", "wrong-task-id", "found", evidence, source_id=1)
            with patch("emberos.tools._get_notes_manager", return_value=notes), \
                 patch("emberos.multi_agent.retrieve_evidence", return_value=forged):
                result = orchestrate_if_requested(fixture[0]["query"], registry)
            assert result["calls"] == [] and result["trace"][-1]["reason"] == "contract_violation", result
            print("PASS retrieval handoff must match the coordinator task ID")

            def forged_proposal(task):
                return AgentResult("os_action", task.task_id, "proposed",
                                   {"name": "delete_file", "arguments": {}},
                                   source_id=task.payload["source_id"])

            with patch("emberos.tools._get_notes_manager", return_value=notes), \
                 patch("emberos.multi_agent.propose_live_read", side_effect=forged_proposal):
                result = orchestrate_if_requested(fixture[0]["query"], registry)
            assert [call["name"] for call in result["calls"]] == ["search_notes"], result
            assert result["reason"] == "tool_proposal_rejected", result
            print("PASS unapproved OS action proposal is rejected before execution")

        with TemporaryDirectory(prefix="ember-multi-agent-dispatch-") as directory:
            registry, _, notes, fixed = _context(directory, ToolRegistry, fixture[0])
            with patch("emberos.tools._get_notes_manager", return_value=notes), \
                 patch.object(registry.get_tool("ram_status"), "func", return_value=fixed["ram_status"]):
                simple = orchestrate_if_requested("How much RAM do I have?", registry)
                assert simple is None, simple
                result = handle_request(fixture[0]["query"], registry, registry.memory,
                                        reply_renderer=_NoReplyModel())
            assert result["route"] == "multi_agent" and result["reply"]["source"] == "original", result
            assert "Current RAM (ram_status)" in result["display_response"], result
            with registry.memory._connect() as db:
                row = db.execute("SELECT is_recall FROM turns WHERE request = ? ORDER BY id DESC LIMIT 1",
                                 (fixture[0]["query"],)).fetchone()
            assert row is not None and row[0] == 1, row

        with TemporaryDirectory(prefix="ember-multi-agent-clarify-") as directory:
            unresolved_case = next(case for case in fixture if "network status" in case["query"])
            registry, _, notes, _ = _context(directory, ToolRegistry, unresolved_case)
            with patch("emberos.tools._get_notes_manager", return_value=notes):
                result = handle_request(unresolved_case["query"], registry, registry.memory,
                                        reply_renderer=_NoReplyModel())
            assert result["route"] == "multi_agent" and not result["calls"], result
            assert result["reply"]["source"] == "original", result
            print("PASS unsupported composite requests return clarification without reply generation")

    print("PASS simple requests defer to the legacy router; app integration starts no reply model")


if __name__ == "__main__":
    main()
