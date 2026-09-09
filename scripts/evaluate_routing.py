"""Evaluate actual Needle routing with a recording executor; no real actions."""

import argparse
import inspect
import json
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
    args = parser.parse_args()
    cases = json.loads((ROOT / "experiments/routing_cases.json").read_text(encoding="utf-8"))
    registry = ToolRegistry()
    results = []
    for case in cases:
        calls = []
        raw_results = []
        model_inputs = []
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
        with patch.object(registry, "execute_tool", side_effect=record), patch.object(
            needle_router.needle_agent, "complete", side_effect=capture
        ):
            response = needle_router.route_and_execute(case["query"], registry)
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
        results.append({**case, "passed": passed, "calls": calls, "response": response,
                        "model_inputs": model_inputs, "raw_model_results": raw_results,
                        "elapsed_seconds": round(time.perf_counter()-started, 3)})
        print(f"{'PASS' if passed else 'FAIL'} {case['query']}", flush=True)
    report = {"needle_version": needle.__version__, "confidence_threshold": needle_router.CONFIDENCE_THRESHOLD,
              "ordered_schemas": [tool._needle_tool for tool in needle_router.ALL_TOOLS],
              "passed": sum(row["passed"] for row in results), "total": len(results),
              "unexpected_actions": sum(row["expected"] is None and bool(row["calls"]) for row in results),
              "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{report['passed']}/{report['total']} matched expectations; {report['unexpected_actions']} unexpected actions. Report: {args.output}")


if __name__ == "__main__":
    main()
