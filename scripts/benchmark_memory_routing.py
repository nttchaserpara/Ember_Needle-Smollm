"""Run the synthetic routing evaluator and record Linux peak RSS and wall time."""

import argparse
import json
from pathlib import Path
import resource
import subprocess
import sys
import time


def main():
    if not sys.platform.startswith("linux"):
        raise SystemExit("This benchmark uses Linux peak RSS units.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Evaluator JSON report path")
    report = parser.parse_args().output.resolve()
    root = Path(__file__).resolve().parents[1]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    started = time.monotonic()
    run = subprocess.run([sys.executable, "scripts/evaluate_memory.py", "--with-routing",
                          "--output", str(report)], cwd=root)
    metrics = {
        "commit": commit,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "max_rss_mib": round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 2),
        "exit_code": run.returncode,
    }
    resources = report.with_name(report.stem + "-resources.json")
    resources.parent.mkdir(parents=True, exist_ok=True)
    resources.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, indent=2))
    return run.returncode


if __name__ == "__main__":
    sys.exit(main())
