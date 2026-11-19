"""Evaluate English file requests through handle_request with a recording executor."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", type=Path, default=ROOT, help="Checkout of the application to evaluate")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.app_root.resolve()))
    cases = json.loads((ROOT / "scripts/fixtures/file_action_language.json").read_text())
    rows = []
    with tempfile.TemporaryDirectory() as directory:
        os.environ["EMBER_DATA_DIR"] = directory
        os.environ["NEEDLE_TELEMETRY"] = "0"
        import run_ember
        from emberos.tools import ToolRegistry, ToolResult
        from test_confirmation import StubRenderer

        target = Path(directory) / "draft.txt"
        target.write_text("Synthetic draft.")
        registry = ToolRegistry()
        for index, case in enumerate(cases):
            expected = case["expected"]
            calls = []

            def record(name, params):
                calls.append({"name": name, "arguments": params})
                return ToolResult(True, result="Synthetic result.")

            def resolve(reference):
                return [str(target)] if reference == target.name else []

            with patch.object(registry, "execute_tool", side_effect=record), patch.object(
                run_ember, "_resolve_file_reference", side_effect=resolve
            ):
                result = run_ember.handle_request(case["query"], registry, session_id=f"case-{index}",
                                                  reply_renderer=StubRenderer())
            route = result["route"]
            status = result.get("status")
            reason = result.get("reason")
            if expected == "negated":
                passed = route == "no_action" and reason == "negated_request" and not calls
            elif expected == "clarify":
                passed = status in {"unresolved_reference", "clarification_required"} and not calls
            else:
                params = {"path": str(Path(directory) / case["file"])}
                if expected == "confirm":
                    passed = (route == "confirmation_required" and result.get("tool") == "delete_file"
                              and result.get("arguments") == params and not calls)
                else:
                    passed = route == "tool_call" and calls == [{"name": "read_file", "arguments": params}]
            rows.append({**case, "passed": passed, "route": route, "status": status, "reason": reason,
                         "calls": calls, "tool": result.get("tool"), "arguments": result.get("arguments")})
            print(f"{'PASS' if passed else 'FAIL'} {case['query']} ({route}, {status or reason})", flush=True)

    restrained = [row for row in rows if row["expected"] in {"negated", "clarify"}]
    report = {"passed": sum(row["passed"] for row in rows), "total": len(rows),
              "no_action_cases": len(restrained), "false_actions": sum(bool(row["calls"]) for row in restrained),
              "unexpected_confirmations": sum(row["route"] == "confirmation_required" for row in restrained),
              "results": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{report['passed']}/{len(rows)} passed, {report['false_actions']}/{len(restrained)} false actions, "
          f"{report['unexpected_confirmations']} unexpected confirmations. Report: {args.output}")
    return int(report["passed"] != len(rows))


if __name__ == "__main__":
    raise SystemExit(main())
