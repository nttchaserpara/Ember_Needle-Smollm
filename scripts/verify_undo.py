"""Exercise real routing, undo and replies with an isolated task database."""

import argparse
import json
from pathlib import Path
import platform
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psutil
from emberos.benchmark import RequestMemorySampler
from emberos.memory import ConversationMemory
from emberos.tools import ToolRegistry, ToolResult
from run_ember import handle_request
from use_cases.tasks import TaskManager


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    process = psutil.Process()
    with tempfile.TemporaryDirectory(prefix="ember-undo-") as directory:
        manager = TaskManager(str(Path(directory) / "tasks.sqlite3"))
        try:
            memory = ConversationMemory(Path(directory) / "conversation.sqlite3")
            registry = ToolRegistry(memory=memory)
            with patch("emberos.tools._get_task_manager", return_value=manager), patch("emberos.tools._log_tool_call"):
                first = registry.execute_tool("add_task", {"title": "Keep this earlier task"})
                last = registry.execute_tool("add_task", {"title": "Undo this last task"})
                if not first.success or not last.success:
                    raise RuntimeError("Could not prepare the temporary tasks")
                execute = registry.execute_tool
                attempts = []

                def guarded(name, params):
                    attempts.append({"name": name, "arguments": params})
                    if name != "undo_last_action":
                        return ToolResult(False, error="Verification blocks all other tool execution.")
                    return execute(name, params)

                rows = []
                for turn in range(2):
                    started = time.perf_counter()
                    with RequestMemorySampler(process) as sample, patch.object(registry, "execute_tool", side_effect=guarded):
                        result = handle_request("undo it", registry, memory)
                    last_gone = manager.get(last.data["id"]) is None
                    first_kept = manager.get(first.data["id"]) is not None
                    passed = (last_gone and first_kept and result.get("tool") == "undo_last_action"
                              and result.get("success") is (turn == 0)
                              and memory.search()[0]["response"] == result.get("display_response")
                              and len(attempts) == turn + 1)
                    rows.append({"turn": turn + 1, "passed": passed, "result": result,
                                 "earlier_task_preserved": first_kept, "last_task_removed": last_gone,
                                 "elapsed_seconds": round(time.perf_counter() - started, 3),
                                 "peak_agent_and_children_mib": round(sample.peak_total / 2**20, 2)})
                    print(result.get("display_response", result.get("response", "No response")), flush=True)
                    print(f"Turn {turn + 1}: {'PASS' if passed else 'FAIL'}", flush=True)
                children = [child.pid for child in process.children(recursive=True)]
                report = {"platform": platform.platform(), "passed": all(row["passed"] for row in rows) and not children,
                          "results": rows, "attempted_calls": attempts, "children_still_running": children,
                          "scope": "Real task database, Needle and SmolLM in an isolated fixture; no native device actions."}
        finally:
            manager.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Report: {args.output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
