"""Evaluate actual Needle routing with a recording executor; no real actions."""

import argparse
from contextlib import nullcontext
import inspect
import json
import platform
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import needle
import needle_router
from emberos.tools import ToolRegistry, ToolResult


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=ROOT / "experiments/routing_cases.json",
                        help="Routing fixture file; execution is always replaced by a recorder")
    parser.add_argument("--without-memory", action="store_true",
                        help="Disable conversation storage; production intent guards remain enabled")
    parser.add_argument("--catalogue-only", action="store_true",
                        help="Offline diagnostic: bypass the history guard (the old --without-memory behavior)")
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    # A sentinel enables history selection without opening any user database.
    # All execution, including history reads, remains replaced by record().
    registry = ToolRegistry(memory=None if args.without_memory else object())
    results = []
    for case in cases:
        calls = []
        raw_results = []
        model_inputs = []
        diagnostics = {}
        def record(name, params):
            calls.append({"name": name, "arguments": params})
            return ToolResult(True, result="Evaluation only; no action performed.")
        complete = needle_router.needle_agent.complete
        def capture(query):
            model_inputs.append(query)
            raw = complete(query)
            raw_results.append(raw)
            return raw
        started = time.perf_counter()
        guard = patch("emberos.history_routing.select_history", return_value={"function_calls": []}) if args.catalogue_only else nullcontext()
        with guard, patch.object(registry, "execute_tool", side_effect=record), patch.object(
            needle_router.needle_agent, "complete", side_effect=capture
        ):
            response = needle_router.route_and_execute(case["query"], registry, diagnostics=diagnostics)
        expected = case["expected"]
        if expected is None:
            passed = not calls
        else:
            def canonical(call):
                bound = inspect.signature(registry.get_tool(call["name"]).func).bind(**call["arguments"])
                bound.apply_defaults()
                return {"name": call["name"], "arguments": bound.arguments}
            try:
                passed = len(calls) == 1 and canonical(calls[0]) == canonical(expected)
            except (TypeError, AttributeError):
                passed = False
        if "expected_route" in case:
            passed = passed and response.get("route") == case["expected_route"]
        results.append({**case, "passed": passed, "calls": calls, "response": response,
                        "model_inputs": model_inputs, "raw_model_results": raw_results,
                        "routing_diagnostics": diagnostics,
                        "elapsed_seconds": round(time.perf_counter()-started, 3)})
        print(f"{'PASS' if passed else 'FAIL'} {case['query']}", flush=True)
    report = {"needle_version": needle.__version__, "platform": platform.platform(),
              "python_version": platform.python_version(),
              "history_selection_enabled": not args.catalogue_only,
              "memory_available": not args.without_memory,
              "confidence_threshold": needle_router.CONFIDENCE_THRESHOLD,
              "ordered_schemas": [tool._needle_tool for tool in needle_router.ALL_TOOLS],
              "passed": sum(row["passed"] for row in results), "total": len(results),
              "unexpected_actions": sum(row["expected"] is None and bool(row["calls"]) for row in results),
              "incorrect_proposed_actions": sum(bool(row["calls"]) and not row["passed"] for row in results),
              "wrong_tool_requests": sum(row["expected"] is not None and any(
                  call["name"] != row["expected"]["name"] for call in row["calls"]) for row in results),
              "failed_valid_requests": sum(row["expected"] is not None and not row["passed"] for row in results),
              "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{report['passed']}/{report['total']} matched expectations; "
          f"{report['unexpected_actions']} unexpected proposed actions; "
          f"{report['wrong_tool_requests']} valid requests sent to the wrong tool; "
          f"{report['failed_valid_requests']} failed valid requests. "
          f"No real tools executed. Report: {args.output}")


if __name__ == "__main__":
    main()
