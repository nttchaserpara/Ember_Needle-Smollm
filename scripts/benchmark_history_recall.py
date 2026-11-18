"""Compare the nine synthetic recall checks before and after the traceback fix."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time

import psutil


ROOT = Path(__file__).resolve().parents[1]
REVISIONS = {"before": "f2ed092", "after": "46998d2"}
FILES = ("scripts/evaluate_memory.py", "emberos/__init__.py",
         "emberos/memory.py", "emberos/outcomes.py")
SAMPLE_INTERVAL = 0.01
MIB = 2**20


def snapshot(label, directory):
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", REVISIONS[label] + "^{commit}"], cwd=ROOT, text=True
    ).strip()
    for name in FILES:
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT))
    return commit


def measure(directory):
    report = directory / "checks.json"
    report.unlink(missing_ok=True)
    available = psutil.virtual_memory().available
    swap = psutil.swap_memory().used
    peak = peak_parent = peak_children = samples = 0
    children_seen = set()
    start = time.monotonic()
    with subprocess.Popen(
        [sys.executable, "-B", "scripts/evaluate_memory.py", "--output", str(report)],
        cwd=directory, stdout=subprocess.DEVNULL,
    ) as job:
        process = psutil.Process(job.pid)
        while job.poll() is None:
            try:
                parent = process.memory_info().rss
                children = process.children(recursive=True)
                children_seen.update(children)
                child_rss = sum(child.memory_info().rss for child in children)
            except psutil.NoSuchProcess:
                continue
            samples += 1
            peak_parent = max(peak_parent, parent)
            peak_children = max(peak_children, child_rss)
            peak = max(peak, parent + child_rss)
            available = min(available, psutil.virtual_memory().available)
            time.sleep(SAMPLE_INTERVAL)
    duration = time.monotonic() - start
    swap_delta = psutil.swap_memory().used - swap
    if job.returncode not in (0, 1) or not samples or not report.exists():
        raise RuntimeError(f"Evaluator did not complete a measurable run (exit {job.returncode})")
    checks = json.loads(report.read_text())
    if checks["with_routing"] or checks["total"] != 9:
        raise RuntimeError("Expected the nine recall-only checks")
    return {
        "passed": checks["passed"], "total": checks["total"],
        "failed_cases": [row["name"] for row in checks["results"] if not row["passed"]],
        "peak_tree_rss_mib": round(peak / MIB, 2),
        "peak_parent_rss_mib": round(peak_parent / MIB, 2),
        "peak_children_rss_mib": round(peak_children / MIB, 2),
        "min_system_available_mib": round(available / MIB, 2),
        "system_swap_delta_mib": round(swap_delta / MIB, 2),
        "elapsed_seconds": round(duration, 3), "samples": samples,
        "observed_child_pids": sorted(child.pid for child in children_seen),
        "observed_children_still_running": sorted(child.pid for child in children_seen if child.is_running()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    model_file = Path("/proc/device-tree/model")
    result = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "hardware": model_file.read_text().strip("\0\n") if model_file.exists() else platform.machine(),
        "os": platform.platform(), "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
        "workload": "Fresh evaluator process and synthetic SQLite DB, nine recall checks, no model or routing",
        "context_tokens": None, "inference_threads": None, "sample_sleep_ms": SAMPLE_INTERVAL * 1000,
        "system_total_mib": round(psutil.virtual_memory().total / MIB, 2),
        "runs": [],
    }
    with tempfile.TemporaryDirectory(prefix="ember-history-bench-") as temporary:
        root = Path(temporary)
        result["commits"] = {label: snapshot(label, root / label) for label in REVISIONS}
        for pair in range(1, 4):
            for label in (("before", "after") if pair % 2 else ("after", "before")):
                run = measure(root / label)
                result["runs"].append({"pair": pair, "revision": label, **run})
                print(f"{label} {pair}: {run['passed']}/{run['total']}, "
                      f"RSS {run['peak_tree_rss_mib']} MiB, available min {run['min_system_available_mib']} MiB, "
                      f"swap delta {run['system_swap_delta_mib']:+g} MiB, {run['elapsed_seconds']} s, "
                      f"children remaining {run['observed_children_still_running']}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {args.output}")


if __name__ == "__main__":
    main()
