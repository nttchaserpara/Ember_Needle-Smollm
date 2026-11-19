"""Compare English task-slot and negation routing on baseline and current code."""

import argparse
from io import BytesIO
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile

from benchmark_history_recall import ROOT, sample_job


DEFAULT_BASELINE = "e8228ac"
EVALUATOR = ROOT / "scripts/evaluate_routing.py"
DEFAULT_FIXTURE = ROOT / "scripts/fixtures/nlp_slots_en.json"


def prepare_baseline(directory: Path, baseline: str, fixture: Path) -> tuple[str, Path]:
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", baseline + "^{commit}"], cwd=ROOT, text=True
    ).strip()
    archive_data = subprocess.check_output(
        ["git", "archive", "--format=zip", commit, "--", "*.py", "*.json"], cwd=ROOT
    )
    with zipfile.ZipFile(BytesIO(archive_data)) as archive:
        archive.extractall(directory)
    evaluator_target = directory / "scripts/evaluate_routing.py"
    fixture_target = directory / "scripts/fixtures" / fixture.name
    evaluator_target.parent.mkdir(parents=True, exist_ok=True)
    fixture_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(EVALUATOR, evaluator_target)
    shutil.copy2(fixture, fixture_target)
    return commit, fixture_target


def measure(label: str, checkout: Path, fixture: Path, revision: str, temporary: Path) -> dict:
    report = temporary / f"{label}.json"
    evaluator = checkout / "scripts/evaluate_routing.py"
    stats = sample_job(
        [sys.executable, "-B", str(evaluator), "--cases", str(fixture), "--output", str(report)],
        checkout,
    )
    if not report.exists():
        raise RuntimeError(f"{label} evaluator failed before writing its report")
    scores = json.loads(report.read_text(encoding="utf-8"))
    if stats["exit_code"] != 0:
        raise RuntimeError(f"{label} evaluator exited with code {stats['exit_code']}")
    expected_total = len(json.loads(fixture.read_text(encoding="utf-8")))
    if scores["total"] != expected_total:
        raise RuntimeError(f"Expected {expected_total} routing cases, got {scores['total']}")
    return {"commit": revision, "resources": stats, **scores}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", default=DEFAULT_BASELINE,
                        help=f"Git revision for the before snapshot (default: {DEFAULT_BASELINE})")
    parser.add_argument("--cases", type=Path, default=DEFAULT_FIXTURE,
                        help="Routing fixture file (execution is stubbed by evaluate_routing.py)")
    args = parser.parse_args()
    fixture = args.cases.resolve()
    with tempfile.TemporaryDirectory(prefix="ember-nlp-slots-") as temporary:
        temporary = Path(temporary)
        baseline_checkout = temporary / "before"
        baseline_checkout.mkdir()
        before_commit, baseline_fixture = prepare_baseline(baseline_checkout, args.baseline, fixture)
        after_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        tracked_worktree_diff = subprocess.run(
            ["git", "diff", "--quiet"], cwd=ROOT, check=False
        ).returncode != 0
        staged_diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=ROOT, check=False
        ).returncode != 0
        if tracked_worktree_diff or staged_diff:
            after_commit += "+worktree"
        runs = {
            "before": measure("before", baseline_checkout, baseline_fixture,
                               before_commit, temporary),
            "after": measure("after", ROOT, fixture, after_commit, temporary),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"platform": platform.platform(), "fixture": fixture.name,
                    "baseline": args.baseline, "runs": runs},
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    for label in ("before", "after"):
        run = runs[label]
        scores = run
        resources = run["resources"]
        print(
            f"{label} {run['commit'][:7]}: {scores['passed']}/{scores['total']}, "
            f"unexpected actions {scores['unexpected_actions']}, "
            f"failed valid requests {scores['failed_valid_requests']}, "
            f"RSS {resources['peak_tree_rss_mib']} MiB, "
            f"available min {resources['min_system_available_mib']} MiB, "
            f"swap delta {resources['system_swap_delta_mib']:+g} MiB, "
            f"{resources['elapsed_seconds']} s, "
            f"remaining children {resources['observed_children_still_running']}",
            flush=True,
        )
        for row in scores["results"]:
            if not row["passed"]:
                raw = row.get("raw_model_results") or [{}]
                proposed = raw[-1].get("function_calls", []) if raw else []
                reason = (row.get("response") or {}).get("reason")
                print(
                    f"{label} failed case: {row['query']} | expected={row['expected']} | "
                    f"proposed={proposed} | reason={reason}",
                    flush=True,
                )
    print(f"Report: {args.output}")
    return 0 if runs["after"]["passed"] == runs["after"]["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
