"""Score a small labeled English history-intent and slot fixture without model inference."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from emberos.history_routing import explicit_history_arguments


def evaluate(cases):
    results = []
    for case in cases:
        actual = "other"
        arguments = None
        reason = ""
        try:
            arguments = explicit_history_arguments(case["query"])
            if arguments is not None:
                actual = "history"
        except ValueError as error:
            reason = str(error)
            actual = "abstain"
        route_correct = actual == case["type"]
        slot_correct = case["type"] != "history" or (
            actual == "history" and arguments == case.get("expected")
        )
        safe_non_history = case["type"] in {"other", "abstain"}
        safe = actual != "history" if safe_non_history else True
        reason_correct = ("expected_reason" not in case or reason == case["expected_reason"])
        results.append({
            "query": case["query"], "expected_type": case["type"], "actual_type": actual,
            "expected_arguments": case.get("expected"), "actual_arguments": arguments,
            "reason": reason, "route_correct": route_correct,
            "slot_correct": slot_correct, "safe": safe, "reason_correct": reason_correct,
        })
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "scripts/fixtures/history_language_en.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    fixture = json.loads(args.cases.read_text(encoding="utf-8"))
    results = evaluate(fixture["cases"])
    count = len(results)
    positive = [row for row in results if row["expected_type"] == "history"]
    non_history = [row for row in results if row["expected_type"] in {"other", "abstain"}]
    report = {
        "language": fixture["language"],
        "method": "bounded explicit-history parser; no model or tools invoked",
        "total": count,
        "route_accuracy": sum(row["route_correct"] for row in results) / count,
        "history_slot_exact_match": sum(row["slot_correct"] for row in positive) / len(positive),
        "safe_non_history_rate": sum(row["safe"] for row in non_history) / len(non_history),
        "expected_abstentions": sum(row["type"] == "abstain" for row in fixture["cases"]),
        "abstentions_correct": sum(row["route_correct"] for row in results if row["expected_type"] == "abstain"),
        "false_history_routes": sum(row["expected_type"] != "history" and row["actual_type"] == "history"
                                    for row in results),
        "reason_accuracy_on_abstentions": (
            sum(row["reason_correct"] for row in results if row["expected_type"] == "abstain")
            / max(1, sum(row["expected_type"] == "abstain" for row in results))
        ),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for row in results:
        print(f"{'PASS' if row['route_correct'] and row['slot_correct'] and row['safe'] and row['reason_correct'] else 'FAIL'} "
              f"{row['expected_type']:7} {row['query']}")
    print(f"Route accuracy {report['route_accuracy']:.3f}; slot EM {report['history_slot_exact_match']:.3f}; "
          f"safe non-history {report['safe_non_history_rate']:.3f}; "
          f"false history routes {report['false_history_routes']}")
    print(f"Report: {args.output}")
    return 0 if all(row["route_correct"] and row["slot_correct"] and row["safe"] and row["reason_correct"]
                    for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
