"""Evaluate the real reply renderer with synthetic outcomes; no OS actions."""

import argparse
from copy import deepcopy
import json
import os
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
from emberos.replies import ReplyRenderer
from emberos.tools import ToolRegistry
from run_ember import handle_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", help="Run a single case ID from experiments/reply_cases.json")
    args = parser.parse_args()
    cases = json.loads((Path(__file__).resolve().parents[1] / "experiments/reply_cases.json").read_text(encoding="utf-8"))
    if args.case:
        cases = [case for case in cases if case["id"] == args.case]
        if not cases:
            parser.error("Unknown --case ID")
    renderer = ReplyRenderer()
    results = []
    process = psutil.Process()
    with tempfile.TemporaryDirectory(prefix="ember-replies-") as temporary:
        for number, case in enumerate(cases):
            memory = ConversationMemory(Path(temporary) / f"conversation-{number}.sqlite3")
            for turn in case.get("history", []):
                memory.record(turn["query"], turn["response"], turn["result"])
            registry = ToolRegistry(memory=memory)
            started = time.perf_counter()
            with RequestMemorySampler(process) as sample, patch(
                "run_ember.route_and_execute", return_value=deepcopy(case["result"])
            ) as route, patch.object(registry, "execute_tool", side_effect=AssertionError("No OS actions allowed")):
                result = handle_request(case["query"], registry, memory, reply_renderer=renderer)
            reply = result["reply"]
            outcome_unchanged = all(result.get(key) == value for key, value in case["result"].items())
            memory_matches = memory.search()[0]["response"] == reply["text"]
            generated = reply["source"] == "smollm2"
            expected_generation = case["generate"]
            children = [p.pid for p in process.children(recursive=True)]
            context_matches = len(reply["history_turn_ids"]) == len(case.get("history", []))
            results.append({"id": case["id"], "query": case["query"], "history": case.get("history", []),
                            "input": case["result"], "reply": reply,
                            "generation_expected": expected_generation,
                            "generation_expectation_met": generated == expected_generation,
                            "execution_and_storage_checks_passed": outcome_unchanged and memory_matches and route.call_count == 1 and context_matches,
                            "elapsed_seconds": round(time.perf_counter() - started, 3),
                            "peak_ember_and_children_mib": round(sample.peak_total / 2**20, 2),
                            "ember_mib_at_peak": round(sample.parent_at_peak / 2**20, 2),
                            "children_mib_at_peak": round(sample.children_at_peak / 2**20, 2),
                            "children_still_running": children})
            print(f"{case['id']}: {reply['source']} ({reply['reason'] or 'accepted'}) | {reply['elapsed_seconds']:.2f}s", flush=True)
            print(reply["text"], flush=True)
    report = {"platform": platform.platform(),
              "model": "SmolLM2-135M-Instruct-Q4_K_M", "persistent_worker": os.environ.get("EMBER_LLM_PERSISTENT", "0") == "1",
              "generated": sum(row["reply"]["source"] == "smollm2" for row in results),
              "fallbacks": sum(row["generation_expected"] and row["reply"]["source"] != "smollm2" for row in results),
              "passed": sum(row["generation_expectation_met"] and row["execution_and_storage_checks_passed"] for row in results),
              "total": len(results), "results": results,
              "limitation": "Output checks are not a proof of semantic equivalence. Review originals and candidates; Windows is not a Pi measurement."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{report['generated']} generated, {report['fallbacks']} guarded/runtime fallbacks. Report: {args.output}")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
