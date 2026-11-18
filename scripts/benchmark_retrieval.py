"""Measure relevance and resource use on the same synthetic corpus across revisions."""

import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

from benchmark_history_recall import ROOT, sample_job


BASELINE = "1fd1ddc"
FILES = ["emberos/__init__.py", "emberos/memory.py",
         "emberos/outcomes.py", "emberos/config.py", "use_cases/__init__.py", "use_cases/notes.py"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--suite", choices=("original", "transfer"), default="original")
    parser.add_argument("--baseline", choices=(BASELINE, "62e15ad"), default=BASELINE,
                        help="Compare against original retrieval or the first field-weighted ranker")
    args = parser.parse_args()
    runs = {}
    with tempfile.TemporaryDirectory(prefix="ember-ranking-bench-") as tmp:
        root = Path(tmp)
        before = root / "baseline"
        files = FILES + (["emberos/retrieval.py"] if args.baseline == "62e15ad" else [])
        for name in files:
            target = before / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(subprocess.check_output(["git", "show", f"{args.baseline}:{name}"], cwd=ROOT))
        # Run exactly the same evaluator and judgments against both implementations.
        (before / "scripts" / "fixtures").mkdir(parents=True)
        shutil.copyfile(ROOT / "scripts/evaluate_retrieval.py", before / "scripts/evaluate_retrieval.py")
        if args.suite == "transfer":
            fixture = Path("scripts/fixtures/retrieval_transfer.json")
            shutil.copyfile(ROOT / fixture, before / fixture)
        for label, checkout, rev in (("before", before, args.baseline), ("after", ROOT, "HEAD")):
            path = root / f"{label}.json"
            resources = sample_job([sys.executable, "-B", "scripts/evaluate_retrieval.py",
                                    "--suite", args.suite, "--output", str(path)], checkout)
            report = json.loads(path.read_text(encoding="utf-8"))
            if report["total"] != {"original": 13, "transfer": 12}[args.suite] or resources["exit_code"] != int(report["passed"] != report["total"]):
                raise RuntimeError(f"Incomplete {label} evaluator run")
            commit = subprocess.check_output(["git", "rev-parse", rev], cwd=ROOT, text=True).strip()
            runs[label] = {"commit": commit, "resources": resources, "evaluation": report}
            scores = report["summary"]["all"]
            print(f"{label} {commit[:7]}: {report['passed']}/{report['total']}, "
                  f"P@5={scores['precision_at_5']:.3f}, MRR@5={scores['mrr_at_5']:.3f}, "
                  f"Recall@5={scores['recall_at_5']:.3f}, empty={scores['empty_correct']}/{scores['empty_queries']}, "
                  f"scope leaks={scores['scope_leaks']}, "
                  f"RSS {resources['peak_tree_rss_mib']} MiB, min available {resources['min_system_available_mib']} MiB, "
                  f"swap delta {resources['system_swap_delta_mib']:+g} MiB, {resources['elapsed_seconds']} s, "
                  f"remaining children {resources['observed_children_still_running']}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"platform": platform.platform(), "runs": runs}, indent=2) + "\n",
                           encoding="utf-8")
    print(f"Report: {args.output}")


if __name__ == "__main__":
    main()
