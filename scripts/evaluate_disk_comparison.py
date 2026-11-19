"""Compare one synthetic CLI request across revisions with all device actions stubbed."""

import argparse
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import tempfile
import time
from unittest.mock import patch

from benchmark_history_recall import sample_job

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "538e630"
QUERY = "Compare my current disk space with last time."


def table(free, mount="/"):
    used = 100 - int(free.removesuffix("G"))
    return f"Drive    Size   Used   Free   Use%\n{'-' * 42}\n{mount:<9} 100G    {used}G    {free}   {used}%"


def run_case(registry_type, directory, query=QUERY, old=None, now=None):
    from emberos.memory import ConversationMemory
    from emberos.tools import ToolResult
    from run_ember import handle_request
    from emberos.replies import ReplyRenderer

    path = directory / "synthetic.sqlite3"
    source = {"tool": "disk_usage", "route": "tool_call", "status": "returned", "success": True}
    previous = ConversationMemory(path)
    if old is not None:
        previous.record("How much free disk space do I have?", old, source)
    previous.record("We discussed disk space", "Unverified conversation text", {})
    current = ConversationMemory(path)
    current.record("disk space", table("97G"), source)
    registry = registry_type(memory=current)
    calls = []
    execute = registry.execute_tool

    def synthetic(name, arguments):
        calls.append(name)
        if name == "search_conversation_history" or (name == "disk_usage" and not arguments):
            return execute(name, arguments)
        return ToolResult(False, error="Blocked by synthetic evaluator")

    def no_model():
        raise RuntimeError("No generation worker in this evaluator")

    def simulated_disk():
        return ToolResult(True, result=now) if now else ToolResult(False, error="Unavailable")

    with patch.object(registry.get_tool("disk_usage"), "func", simulated_disk), \
         patch.object(registry, "execute_tool", side_effect=synthetic), \
         patch.object(registry, "execute_tool_chain", side_effect=AssertionError("Unexpected chain")):
        started = time.monotonic()
        result = handle_request(query, registry, current, reply_renderer=ReplyRenderer(client_factory=no_model))
        duration = time.monotonic() - started
    return {"route": result["route"], "answer": result["display_response"], "calls": calls,
            "success": result.get("success", False), "trace": result.get("trace", []),
            "request_seconds": round(duration, 4)}


def worker(directory, report):
    from emberos.tools import ToolRegistry
    result = run_case(ToolRegistry, directory, old=table("40G"), now=table("33G"))
    result["passed"] = (
        result["calls"] == ["search_conversation_history", "disk_usage"]
        and result["success"] and all(x in result["answer"] for x in ("history #1", "40 GiB", "33 GiB", "lower"))
        and "97 GiB" not in result["answer"]
    )
    report.write_text(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--suite", choices=("single", "coordination"), default="single")
    parser.add_argument("--baseline", help="Git revision for the before snapshot (suite default if omitted)")
    parser.add_argument("--worker-root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_root:
        sys.path.insert(0, str(args.worker_root))
        if args.suite == "coordination":
            from disk_coordination_cases import evaluate
            args.output.write_text(json.dumps(evaluate(QUERY, table, run_case)))
        else:
            worker(args.output.parent, args.output)
        return 0

    runs = {}
    baseline = args.baseline or ("cde1aef" if args.suite == "coordination" else BASELINE)
    with tempfile.TemporaryDirectory(prefix="ember-comparison-") as temp:
        previous = Path(temp) / "baseline"
        # Load only tracked Python sources, keeping the user's checkout and DB intact.
        archive = subprocess.check_output(["git", "archive", baseline, "--", "*.py"], cwd=ROOT)
        with tarfile.open(fileobj=BytesIO(archive)) as files:
            for item in files:
                if item.isfile() and item.name.endswith(".py"):
                    target = previous / item.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(files.extractfile(item).read())
        for label, root in (("before", previous), ("after", ROOT)):
            directory = Path(temp) / label
            directory.mkdir()
            report = directory / "case.json"
            environment = {**os.environ, "EMBER_DATA_DIR": str(directory), "NEEDLE_TELEMETRY": "0"}
            stats = sample_job([sys.executable, "-B", str(Path(__file__).resolve()),
                                "--worker-root", str(root), "--suite", args.suite, "--output", str(report)], root, env=environment)
            if stats["exit_code"] or not report.exists():
                raise RuntimeError(f"{label} evaluator did not complete: {stats}")
            rev = baseline if label == "before" else "HEAD"
            commit = subprocess.check_output(["git", "rev-parse", rev], cwd=ROOT, text=True).strip()
            runs[label] = {"commit": commit, **json.loads(report.read_text()), **stats}
            result = runs[label]
            if args.suite == "coordination":
                for metric, counts in {"cases": result["cases"], **result["metrics"]}.items():
                    print(f"{label} {commit[:7]} {metric}: {counts['passed']}/{counts['total']}")
                for row in result["results"]:
                    if not row["passed"]:
                        print(f"FAIL {row['registry']} {row['case']}")
            latency = f"request {result['request_seconds']} s, " if args.suite == "single" else ""
            print(f"{label} {commit[:7]}: {'PASS' if result['passed'] else 'FAIL'}, "
                  f"RSS {stats['peak_tree_rss_mib']} MiB, available min {stats['min_system_available_mib']} MiB, "
                  f"swap delta {stats['system_swap_delta_mib']:+g} MiB, {latency}"
                  f"full run {stats['elapsed_seconds']} s, children remaining {stats['observed_children_still_running']}",
                  flush=True)
    model = Path("/proc/device-tree/model")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"platform": platform.platform(), "python": platform.python_version(),
                                      "hardware": model.read_text().strip("\0\n") if model.exists() else platform.machine(),
                                      "context_tokens": None, "inference_threads": None,
                                      "suite": args.suite, "baseline": baseline,
                                      "query": QUERY if args.suite == "single" else None,
                                      "mock_disk": True, "runs": runs}, indent=2) + "\n")
    print(f"Report: {args.output}")
    return 0 if runs["after"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
