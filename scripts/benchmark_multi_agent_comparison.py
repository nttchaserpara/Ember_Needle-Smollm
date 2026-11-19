"""Compare general multi-agent routing with the pre-feature router."""

import argparse
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile

from benchmark_history_recall import ROOT, sample_job

DEFAULT_BASELINE = "f64a710"
EVALUATOR = ROOT / "scripts/evaluate_multi_agent.py"
FIXTURE = ROOT / "scripts/fixtures/multi_agent_comparison_en.json"


def prepare_baseline(directory, revision):
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", revision + "^{commit}"], cwd=ROOT, text=True
    ).strip()
    archive = subprocess.check_output(
        ["git", "archive", "--format=zip", commit, "--", "*.py"], cwd=ROOT
    )
    directory.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BytesIO(archive)) as sources:
        sources.extractall(directory)
    evaluator_path = directory / "scripts/evaluate_multi_agent.py"
    fixture_path = directory / "scripts/fixtures/multi_agent_comparison_en.json"
    evaluator_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(EVALUATOR, evaluator_path)
    shutil.copy2(FIXTURE, fixture_path)
    return commit


def measure(label, checkout, revision, temporary, expected_total):
    report_path = temporary / f"{label}.json"
    data_dir = temporary / f"data-{label}"
    data_dir.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ,
                   "EMBER_DATA_DIR": str(data_dir), "NEEDLE_TELEMETRY": "0"}
    stats = sample_job(
        [sys.executable, "-B", str(checkout / "scripts/evaluate_multi_agent.py"),
         "--output", str(report_path)],
        checkout, env=environment,
    )
    if stats["exit_code"] != 0 or not report_path.exists():
        raise RuntimeError(f"{label} evaluator did not complete: {stats}")
    score = json.loads(report_path.read_text(encoding="utf-8"))
    if score["total"] != expected_total:
        raise RuntimeError(f"Expected {expected_total} cases for {label}, got {score['total']}")
    return {"commit": revision, "resources": stats, **score}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", default=DEFAULT_BASELINE,
                        help=f"Pre-feature Git revision (default: {DEFAULT_BASELINE})")
    args = parser.parse_args()
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory(prefix="ember-ma-comparison-") as name:
        temporary = Path(name)
        baseline_checkout = temporary / "before"
        baseline_commit = prepare_baseline(baseline_checkout, args.baseline)
        current_commit = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                                 cwd=ROOT, text=True).strip()
        changed_worktree = subprocess.run(["git", "diff", "--quiet"], cwd=ROOT,
                                          check=False).returncode != 0
        changed_index = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT,
                                       check=False).returncode != 0
        if changed_worktree or changed_index:
            current_commit += "+worktree"
        runs = {
            "before": measure("before", baseline_checkout, baseline_commit, temporary, len(cases)),
            "after": measure("after", ROOT, current_commit, temporary, len(cases)),
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"platform": platform.platform(),
                                       "baseline": args.baseline,
                                       "fixture": FIXTURE.name, "runs": runs},
                                      indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    for label in ("before", "after"):
        run = runs[label]
        resources = run["resources"]
        print(f"{label} {run['commit'][:7]}: {run['passed']}/{run['total']}, "
              f"combined {run['combined_success']}/{run['combined_total']}, "
              f"delegation {run['correct_delegation']}/{run['combined_total']}, "
              f"sources {run['source_correct']}/{run['combined_total']}, "
              f"safe stops {run['safe_stops']}/{run['safe_stop_total']}, "
              f"false actions {run['false_actions']}, "
              f"blocked proposals {run['unexpected_tool_proposals']}, "
              f"RSS {resources['peak_tree_rss_mib']} MiB, "
              f"available min {resources['min_system_available_mib']} MiB, "
              f"swap delta {resources['system_swap_delta_mib']:+g} MiB, "
              f"{resources['elapsed_seconds']} s, "
              f"remaining children {resources['observed_children_still_running']}",
              flush=True)
        for row in run["results"]:
            if not row["passed"]:
                print(f"{label} failed: {row['kind']} | {row['query']} | "
                      f"route={row['route']} | calls={row['executed_calls']} | "
                      f"proposals={row['tool_proposals']}", flush=True)
    print(f"Report: {args.output}")
    return 0 if runs["after"]["passed"] == runs["after"]["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
