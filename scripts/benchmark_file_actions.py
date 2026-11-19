"""Compare the same nine synthetic file requests in fresh evaluator processes."""
import argparse
from io import BytesIO
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import zipfile

from benchmark_history_recall import ROOT, sample_job

BASELINE = "88b166b"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runs = {}
    with tempfile.TemporaryDirectory() as temp:
        before = Path(temp) / "baseline"
        sources = subprocess.check_output(
            ["git", "archive", "--format=zip", BASELINE, "--", "*.py"], cwd=ROOT
        )
        with zipfile.ZipFile(BytesIO(sources)) as archive:
            archive.extractall(before)
        for label, checkout, revision in (("before", before, BASELINE), ("after", ROOT, "HEAD")):
            report = Path(temp) / f"{label}.json"
            stats = sample_job([sys.executable, "-B", str(ROOT / "scripts/evaluate_file_actions.py"),
                                "--app-root", str(checkout), "--output", str(report)], checkout)
            if not report.exists():
                raise RuntimeError(f"{label} evaluator failed before writing the report")
            scores = json.loads(report.read_text())
            if stats["exit_code"] != int(scores["passed"] != scores["total"]):
                raise RuntimeError(f"Incomplete {label} evaluation")
            commit = subprocess.check_output(["git", "rev-parse", revision], cwd=ROOT, text=True).strip()
            runs[label] = {"commit": commit, "resources": stats, **scores}
            print(f"{label} {commit[:7]}: {scores['passed']}/{scores['total']}, "
                  f"false actions {scores['false_actions']}/{scores['no_action_cases']}, "
                  f"unexpected confirmations {scores['unexpected_confirmations']}, "
                  f"RSS {stats['peak_tree_rss_mib']} MiB, min available {stats['min_system_available_mib']} MiB, "
                  f"swap delta {stats['system_swap_delta_mib']:+g} MiB, {stats['elapsed_seconds']} s, "
                  f"remaining children {stats['observed_children_still_running']}", flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"platform": platform.platform(), "runs": runs}, indent=2) + "\n")
    print(f"Report: {args.output}")
    return runs["after"]["resources"]["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
