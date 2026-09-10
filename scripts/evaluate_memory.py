"""Check retrieved content and session boundaries using a disposable database.

Add --with-routing to also exercise real Needle inference on fresh requests. Non-history tools are blocked; the user's database is never opened.
"""

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from emberos.memory import ConversationMemory


def seed(path):
    old = ConversationMemory(path)
    old.record("Review documents", "Archive.pdf was discussed.", {"status": "success"})
    previous = ConversationMemory(path)
    previous.record("Review documents", "Scholarship.pdf was discussed.", {"status": "success"})
    previous.record("Check disk space", "27 GB available.", {
        "tool": "disk_usage", "status": "success", "success": True})
    for _ in range(8):
        previous.record("What did we discuss about disk space?",
                        'Traceback: C:\\Users\\example\\Documents\\Ember\\tools.py\nNo module named gpu_detect', {
            "tool": "get_system_info", "status": "error", "success": False})
    previous.record("How much RAM is available?", "180 MiB available.", {"status": "success"})
    previous.record("Show my pending tasks", "Buy milk", {"status": "success"})
    current = ConversationMemory(path)
    current.record("Review documents", "Today.pdf was discussed.", {"status": "success"})
    current.record("Open program", "Program opened.", {"status": "success"})
    return current


def evaluate(memory, with_routing=False):
    cases = [
        ("Previous session content", "documents", "previous_session", ["Scholarship.pdf"], ["Today.pdf", "Archive.pdf", "gpu_detect"]),
        ("Current session content", "documents", "current_session", ["Today.pdf"], ["Scholarship.pdf", "Archive.pdf"]),
        ("All sessions content", "documents", "all", ["Today.pdf", "Scholarship.pdf", "Archive.pdf"], ["gpu_detect"]),
        ("Unknown topic does not return recent history", "volcano", "all", ["No matching"], ["Today.pdf", "27 GB"]),
        ("RAM does not match program", "RAM", "all", ["180 MiB"], ["Program opened"]),
        ("Disk result precedes deduplicated errors", "disk space", "all", ["27 GB", "gpu_detect", "error"], ["Scholarship.pdf"]),
        ("Missing topic in selected session stays empty", "disk space", "current_session", ["No matching"], ["27 GB"]),
    ]
    results = []
    for name, topic, scope, required, forbidden in cases:
        output = memory.recall(topic, scope=scope)
        passed = all(text in output for text in required) and all(text not in output for text in forbidden)
        if topic == "disk space" and scope == "all":
            passed &= output.count("gpu_detect") == 1 and output.index("27 GB") < output.index("gpu_detect")
        results.append({"name": name, "passed": passed, "output": str(output), "retrieval": output.data})
    # Reproduce pasted terminal output saved as an ordinary user request.
    # Literal matching still mistakes a directory name for a discussion topic.
    # Keep this failure visible instead of testing only clean synthetic chats.
    memory.record('File "C:\\Users\\example\\Documents\\Ember\\tools.py", line 109, in execute_tool',
                  "I couldn't reliably identify the requested action.",
                  {"route": "unresolved_tool_request", "status": "low_confidence"})
    output = memory.recall("documents")
    results.append({"name": "Pasted traceback is not a document discussion",
                    "passed": "tools.py" not in output, "output": str(output),
                    "retrieval": output.data, "known_limitation": "Literal lookup lacks semantic relevance filtering"})
    if not with_routing:
        return results

    from unittest.mock import patch
    from emberos.tools import ToolRegistry, ToolResult
    from needle_router import route_and_execute

    # Every input is independent; no numbered answers or assumed menu state.
    routing_cases = [
        ("Which documents did we talk about last time?", "search_conversation_history", ["Scholarship.pdf"], ["Today.pdf", "Archive.pdf", "gpu_detect", "tools.py"]),
        ("How much free disk space do I have?", "disk_usage", [], []),
        ("Show my recent conversations.", "search_conversation_history", ["Program opened"], []),
        ("disk space", "disk_usage", [], []),
        ("Could you show the messages about disk space?", "search_conversation_history", ["27 GB"], ["Scholarship.pdf"]),
        ("What did we discuss about RAM?", "search_conversation_history", ["180 MiB"], ["Program opened"]),
        ("What did we discuss about my pending tasks?", "search_conversation_history", ["Buy milk"], ["Scholarship.pdf"]),
    ]
    registry = ToolRegistry(memory=memory)
    original_execute = registry.execute_tool
    for query, expected_tool, required, forbidden in routing_cases:
        calls = []

        def history_only(name, arguments):
            calls.append({"name": name, "arguments": arguments})
            if name != "search_conversation_history":
                return ToolResult(True, result="Evaluation stub; no device action performed.")
            return original_execute(name, arguments)

        diagnostics = {}
        with patch.object(registry, "execute_tool", side_effect=history_only):
            final = route_and_execute(query, registry, diagnostics=diagnostics)
        output = str(final.get("result", ""))
        passed = (len(calls) == 1 and calls[0]["name"] == expected_tool
                  and all(text in output for text in required)
                  and all(text not in output for text in forbidden))
        results.append({"name": query, "passed": passed, "expected_tool": expected_tool,
                        "final": final, "calls": calls, "diagnostics": diagnostics})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--with-routing", action="store_true")
    args = parser.parse_args()
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="ember-memory-eval-") as directory:
        results = evaluate(seed(Path(directory) / "history.sqlite3"), args.with_routing)
    report = {"with_routing": args.with_routing, "passed": sum(row["passed"] for row in results),
              "total": len(results), "elapsed_seconds": round(time.perf_counter() - started, 3),
              "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for row in results:
        print(f"{'PASS' if row['passed'] else 'FAIL'} {row['name']}")
    print(f"{report['passed']}/{report['total']} content/routing checks passed. Report: {args.output}")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
