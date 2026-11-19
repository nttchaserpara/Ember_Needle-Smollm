"""Measure the general multi-agent fixture and process-tree resources."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys

from benchmark_history_recall import ROOT, SAMPLE_INTERVAL, sample_job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error("--repeats must be from 1 to 10")

    model_file = Path("/proc/device-tree/model")
    hardware = model_file.read_text().strip("\0\n") if model_file.exists() else platform.machine()
    command = [sys.executable, "-B", "scripts/test_multi_agent.py"]
    runs = []
    for number in range(1, args.repeats + 1):
        resources = sample_job(command, ROOT)
        run = {"run": number, **resources}
        runs.append(run)
        print(f"run {number}: exit={resources['exit_code']}, "
              f"RSS {resources['peak_tree_rss_mib']} MiB, "
              f"available min {resources['min_system_available_mib']} MiB, "
              f"swap delta {resources['system_swap_delta_mib']:+g} MiB, "
              f"{resources['elapsed_seconds']} s, "
              f"children remaining {resources['observed_children_still_running']}")
        if resources["exit_code"] != 0:
            raise RuntimeError(f"multi-agent fixture failed on run {number}")

    report = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "hardware": hardware,
        "os": platform.platform(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "workload": "Synthetic multi-agent tests; mocked live reads; temporary note/history databases; no user data changed",
        "comparison": "Current feature only; no equivalent general multi-agent workload existed before this change.",
        "sample_interval_ms": SAMPLE_INTERVAL * 1000,
        "runs": runs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {args.output}")


if __name__ == "__main__":
    main()
