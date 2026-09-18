"""Capture raw Needle diagnostics for the RAM/CPU-info routing confusion,
repeated N times per query, on this platform. Run on Windows AND Pi
separately -- do not guess a fix before this data exists.
"""

import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from emberos.tools import ToolRegistry
from needle_router import route_and_execute

QUERIES = [
    "show ram status and disk space",
    "how much RAM is available right now?",
    "what is cpu info and system uptime",
    "show ram status",       # single-intent control
    "check ram usage",       # rephrase control
    "cpu info and uptime",   # shorter compound control
]

REPEATS = 5


def main():
    registry = ToolRegistry(memory=None)
    results = []
    for query in QUERIES:
        for run in range(REPEATS):
            diagnostics = {}
            outcome = route_and_execute(
                query, registry, diagnostics=diagnostics, multi_step_mode=True
            )
            raw = diagnostics.get("raw_model_result", {})
            row = {
                "query": query,
                "run": run,
                "route": outcome.get("route"),
                "confidence": outcome.get("confidence") or outcome.get("needle_confidence"),
                "function_calls": raw.get("function_calls"),
                "model_success": raw.get("success"),
                "model_error": raw.get("error"),
                "reasoning": raw.get("reasoning"),
            }
            results.append(row)
            print(f"{query!r} run={run} -> route={row['route']} "
                  f"conf={row['confidence']} calls={row['function_calls']} "
                  f"model_error={row['model_error']}")

    out = ROOT / "logs" / f"ram_routing_diag_{platform.machine()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()