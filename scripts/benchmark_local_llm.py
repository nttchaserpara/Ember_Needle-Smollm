"""Measure an Ember document job including its native child process (no downloads)."""

import argparse
import json
import os
import platform
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document", type=Path)
    args = parser.parse_args()
    process = psutil.Process()
    stop = threading.Event()
    stats = {"peak_tree_rss_mib": 0, "peak_parent_rss_mib": 0, "peak_children_rss_mib": 0,
             "minimum_system_available_mib": float("inf"), "observed_child_pids": []}
    child_ids = set()

    def sample():
        while not stop.is_set():
            parent_rss = process.memory_info().rss
            child_rss = 0
            for child in process.children(recursive=True):
                try:
                    child_ids.add(child.pid)
                    child_rss += child.memory_info().rss
                except psutil.NoSuchProcess:
                    pass
            stats["peak_tree_rss_mib"] = max(stats["peak_tree_rss_mib"], (parent_rss + child_rss) / 2**20)
            stats["peak_parent_rss_mib"] = max(stats["peak_parent_rss_mib"], parent_rss / 2**20)
            stats["peak_children_rss_mib"] = max(stats["peak_children_rss_mib"], child_rss / 2**20)
            stats["minimum_system_available_mib"] = min(stats["minimum_system_available_mib"], psutil.virtual_memory().available / 2**20)
            stop.wait(0.02)

    monitor = threading.Thread(target=sample, daemon=True)
    monitor.start()
    started = time.perf_counter()
    swap_before = psutil.swap_memory().used
    try:
        from needle_router import route_and_execute
        from emberos.tools import ToolRegistry
        registry = ToolRegistry()
        with tempfile.TemporaryDirectory() as temporary:
            document = args.document.resolve() if args.document else Path(temporary) / "report.txt"
            if not args.document:
                document.write_text(
                    "The team tested the audio tool on Friday. All ten checks passed. "
                    "The next test is scheduled for Monday.", encoding="utf-8"
                )
            result = route_and_execute(f'Summarize "{document}"', registry)
    finally:
        stop.set()
        monitor.join(timeout=2)
    stats.update({
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "model": "SmolLM2-135M-Instruct-Q4_K_M.gguf",
        "context_tokens": int(os.environ.get("EMBER_LLM_CONTEXT", 2048)),
        "generation_threads": int(os.environ.get("EMBER_LLM_THREADS", 4)),
        "elapsed_seconds": time.perf_counter() - started,
        "parent_rss_after_mib": process.memory_info().rss / 2**20,
        "system_swap_change_mib": (psutil.swap_memory().used - swap_before) / 2**20,
        "observed_child_pids": sorted(child_ids),
        "children_still_running": [child.pid for child in process.children(recursive=True)],
        "torch_imported": "torch" in sys.modules,
        "transformers_imported": "transformers" in sys.modules,
        "result": result,
    })
    for key, value in stats.items():
        if isinstance(value, float):
            stats[key] = round(value, 2)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
