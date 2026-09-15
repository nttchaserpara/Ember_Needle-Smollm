"""Measure production replies with per-job unload, reuse, or idle sleep/wake.

No real tools run. Needle is loaded for RSS accounting, but routing latency is
excluded. Both lifecycle modes use the same ReplyRenderer and llama-server.
"""

import argparse
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import psutil

from emberos.benchmark import RequestMemorySampler
from emberos.memory import ConversationMemory
from emberos.replies import ReplyRenderer, REPLY_RULE
from use_cases.local_llm import LocalTextClient, GenerationError, shutdown_shared_worker


def snapshot():
    parent = psutil.Process()
    children = []
    for child in parent.children(recursive=True):
        try:
            children.append({"pid": child.pid, "rss_mib": child.memory_info().rss / 2**20})
        except psutil.NoSuchProcess:
            pass
    parent_rss = parent.memory_info().rss / 2**20
    child_rss = sum(child["rss_mib"] for child in children)
    return {"parent_rss_mib": parent_rss, "children_rss_mib": child_rss,
            "tree_rss_mib": parent_rss + child_rss, "children": children,
            "system_available_mib": psutil.virtual_memory().available / 2**20,
            "system_swap_mib": psutil.swap_memory().used / 2**20}


def measure_reply(case, factory):
    # Isolate fixture history per request, without opening the user's database.
    with tempfile.TemporaryDirectory(prefix="ember-worker-bench-") as directory:
        memory = ConversationMemory(Path(directory) / "history.sqlite3")
        for turn in case.get("history", []):
            memory.record(turn["query"], turn["response"], turn["result"])
        before = snapshot()
        with RequestMemorySampler() as sample:
            reply = ReplyRenderer(client_factory=factory).render(
                case["result"], query=case["query"], memory=memory)
        after = snapshot()
    generation = reply.get("generation") or {}
    return {"case": case["id"], "reply": reply, "before": before, "after": after,
            "generation_completed": generation.get("stop_type") in ("eos", "word")
                and not generation.get("truncated")
                and reply["reason"] not in ("generation_failed", "generation_cancelled"),
            "peak_tree_rss_mib": sample.peak_total / 2**20,
            "parent_at_peak_mib": sample.parent_at_peak / 2**20,
            "children_at_peak_mib": sample.children_at_peak / 2**20}


def idle_window(client, seconds):
    """Only /props polling: it neither wakes b7898 nor resets its idle timer."""
    samples = []
    started = time.monotonic()
    next_progress = 0
    while True:
        props = client._request("/props", timeout=5)
        elapsed = time.monotonic() - started
        samples.append({"elapsed_seconds": elapsed, "is_sleeping": props.get("is_sleeping"),
                        **snapshot()})
        if not isinstance(props.get("is_sleeping"), bool):
            raise GenerationError("Runtime /props has no is_sleeping flag; sleep support cannot be verified")
        if elapsed >= next_progress:
            print(f"Idle {elapsed:.0f}/{seconds}s: sleeping={props['is_sleeping']}, "
                  f"worker RSS={samples[-1]['children_rss_mib']:.1f} MiB", flush=True)
            next_progress += 15
        if elapsed >= seconds:
            return samples
        time.sleep(min(1, seconds - elapsed))


def run_mode(mode, cases, count, sleep_idle, idle_grace):
    shutdown_shared_worker()
    persistent = mode != "load-per-call"
    timeout = float(os.environ.get("EMBER_REPLY_TIMEOUT", 45))

    def factory():
        return LocalTextClient(persistent=persistent, sleep_idle=sleep_idle,
                               grounding_rule=REPLY_RULE, request_timeout=timeout,
                               startup_timeout=timeout, job_timeout=timeout)

    report = {"mode": mode, "sleep_idle_seconds": sleep_idle, "calls": [], "checks_passed": False}
    try:
        if mode != "sleep-wake":
            for i in range(count):
                row = measure_reply(cases[i % len(cases)], factory)
                report["calls"].append(row)
                print(f"{mode} {i + 1}/{count}: {row['reply']['elapsed_seconds']:.2f}s, "
                      f"{row['reply']['source']} ({row['reply']['reason'] or 'accepted'})", flush=True)
            report["checks_passed"] = all(row["generation_completed"] for row in report["calls"])
            if not persistent:
                report["checks_passed"] &= all(not row["after"]["children"] for row in report["calls"])
        else:
            # Identical input isolates cold/warm/wake cost from prompt length.
            for phase in ("cold", "warm"):
                row = measure_reply(cases[0], factory)
                row["phase"] = phase
                report["calls"].append(row)
            if not all(row["generation_completed"] for row in report["calls"]):
                raise GenerationError("Cold/warm generation did not finish; no valid sleep comparison")
            with LocalTextClient(persistent=True, sleep_idle=sleep_idle,
                                 job_timeout=sleep_idle + idle_grace + 30) as observer:
                observer._start()
                props = observer._request("/props", timeout=5)
                report["runtime_build"] = props.get("build_info")
                report["idle_worker_pid"] = observer.process.pid
                report["idle_samples"] = idle_window(observer, sleep_idle + idle_grace)
            sleeping = [row for row in report["idle_samples"] if row["is_sleeping"]]
            report["sleep_observed"] = bool(sleeping)
            report["before_wake"] = snapshot()
            wake = measure_reply(cases[0], factory)
            wake["phase"] = "wake"
            report["calls"].append(wake)
            warm = report["calls"][1]
            report["wake_minus_warm_seconds"] = wake["reply"]["elapsed_seconds"] - warm["reply"]["elapsed_seconds"]
            if sleeping:
                report["minimum_sleeping_worker_rss_mib"] = min(row["children_rss_mib"] for row in sleeping)
                report["observed_worker_rss_drop_mib"] = (
                    warm["after"]["children_rss_mib"] - report["minimum_sleeping_worker_rss_mib"])
            pid = report["idle_worker_pid"]
            report["same_worker_reused"] = all(
                [child["pid"] for child in row["after"]["children"]] == [pid]
                for row in report["calls"])
            report["checks_passed"] = report["sleep_observed"] and report["same_worker_reused"] and wake["generation_completed"]
    except (GenerationError, OSError, ValueError, psutil.Error) as exc:
        report["error"] = str(exc)
    finally:
        shutdown_shared_worker()
        report["after_shutdown"] = snapshot()
        report["checks_passed"] &= not report["after_shutdown"]["children"]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("load-per-call", "keep-loaded", "both", "sleep-wake"), default="both")
    parser.add_argument("--n", type=int, default=10, help="Calls per mode; sleep-wake always runs cold, warm, wake")
    parser.add_argument("--sleep-idle", type=int, default=int(os.environ.get("EMBER_LLM_SLEEP_IDLE", 90)))
    parser.add_argument("--idle-grace", type=int, default=15, help="Seconds to sample beyond the idle threshold")
    parser.add_argument("--output", type=Path, default=ROOT / "logs/reply-worker-benchmark.json")
    args = parser.parse_args()
    if args.n < 1 or args.sleep_idle < 1 or args.idle_grace < 1:
        parser.error("--n, --sleep-idle and --idle-grace must be positive")
    # Load the production router for honest parent RSS; no query is executed.
    import needle_router
    cases = [case for case in json.loads((ROOT / "experiments/reply_cases.json").read_text(encoding="utf-8")) if case["generate"]]
    config = LocalTextClient()
    report = {"platform": platform.platform(), "python_version": platform.python_version(),
              "needle_imported": True, "model": str(config.model_path),
              "context_tokens": config.context_size, "generation_threads": config.threads,
              "scope": "Production reply renderer with fixture outcomes/history; no tool execution or routing time.",
              "limitation": "Summed RSS can double-count shared pages. Generation completion is not factual correctness.",
              "results": []}
    modes = ("load-per-call", "keep-loaded") if args.mode == "both" else (args.mode,)
    interrupted = False
    try:
        for mode in modes:
            report["results"].append(run_mode(mode, cases, args.n, args.sleep_idle, args.idle_grace))
    except KeyboardInterrupt:
        interrupted = True
        report["interrupted"] = True
    finally:
        shutdown_shared_worker()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report: {args.output}")
    return 130 if interrupted else (0 if all(row["checks_passed"] for row in report["results"]) else 1)


if __name__ == "__main__":
    raise SystemExit(main())
