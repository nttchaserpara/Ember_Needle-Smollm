#!/usr/bin/env python3
"""
bench_short_response.py — EmberOS-Pi benchmark for short, frequent
natural-language tool-confirmation responses.

Tests the SPECIFIC workload from the roadmap note:
  "generate pendek tapi setiap kali ada tool call"
as opposed to the one-off long-document summarize workload already
measured (204.85 MiB peak RSS / 12.18s for a 107-byte file).

Measures, for EACH of N short prompts, in BOTH modes:
  - load-per-call : spawn llama-cli fresh, run one generation, exit
                     (mirrors current production behaviour for summarize)
  - keep-loaded   : one llama-server process stays resident for the
                     whole run; each prompt is a request over HTTP

For each call, records:
  wall latency (s), peak RSS during the call (MiB), whole-system RAM
  used/available (MiB), and the raw generated text (for manual
  faithfulness scoring — see the note printed at the end).

Results are appended to a CSV automatically — no manual copy-paste
into a spreadsheet needed.

======================== EDIT BEFORE RUNNING ==========================
Update the paths below to match your actual Pi setup (model file,
llama.cpp binaries). Everything else has sensible defaults.
========================================================================
"""

import argparse
import csv
import os
import statistics
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

try:
    import requests
except ImportError:
    raise SystemExit(
        "Missing 'requests'. Install with:\n"
        "  ./venv/bin/pip install requests --break-system-packages"
    )

# ---------------------------------------------------------------------
# EDIT ME — match these to your actual Pi paths / current model
# ---------------------------------------------------------------------
LLAMA_CLI_BIN    = os.path.expanduser("~/Ember_Needle-Smollm/runtimes/llama.cpp/build/bin/llama-cli")
LLAMA_SERVER_BIN = os.path.expanduser("~/Ember_Needle-Smollm/runtimes/llama.cpp/build/bin/llama-server")
MODEL_PATH       = os.path.expanduser("~/Ember_Needle-Smollm/models/SmolLM2-135M-Instruct-Q4_K_M.gguf")
SERVER_HOST      = "127.0.0.1"
SERVER_PORT      = 8765
N_THREADS        = os.cpu_count() or 4
MAX_TOKENS       = 40          # short confirmations only, not a summary
RESULTS_CSV      = "bench_short_response_results.csv"
# ---------------------------------------------------------------------

# Representative short tool-confirmation prompts — mirrors a turn that
# happens on EVERY tool call (mute, brightness, task, delete...), not
# the rare long-document summarize case.
PROMPTS = [
    ("mute_volume",      "The mute_volume tool ran successfully. In one short sentence, confirm this to the user."),
    ("volume_up",        "The volume_up tool ran successfully, volume increased by 2 steps. In one short sentence, confirm this to the user."),
    ("dark_mode_on",     "The set_dark_mode tool ran successfully, dark mode is now enabled. In one short sentence, confirm this to the user."),
    ("task_added",       "The add_task tool ran successfully. A task titled 'Buy groceries' was added. In one short sentence, confirm this to the user."),
    ("file_deleted",     "The delete_file tool ran successfully. The file 'report.pdf' was deleted. In one short sentence, confirm this to the user."),
    ("battery_status",   "The battery_status tool returned: 82% remaining, plugged in. In one short sentence, relay this to the user."),
    ("brightness_set",   "The set_brightness tool ran successfully, brightness set to 60%. In one short sentence, confirm this to the user."),
    ("screenshot_taken", "The take_screenshot tool ran successfully. In one short sentence, confirm this to the user."),
    ("task_completed",   "The complete_task tool ran successfully. Task 'Buy groceries' marked done. In one short sentence, confirm this to the user."),
    ("unmute",           "The mute_volume tool ran successfully (toggled back to unmuted). In one short sentence, confirm this to the user."),
]


def _sample_peak_rss(proc: psutil.Process, stop_event: threading.Event, out: dict, interval: float = 0.05):
    """Background sampler: records the peak RSS (MiB) of proc + its children."""
    peak = 0.0
    while not stop_event.is_set():
        try:
            children = proc.children(recursive=True)
            total = proc.memory_info().rss
            for c in children:
                try:
                    total += c.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            peak = max(peak, total / (1024 * 1024))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            break
        time.sleep(interval)
    out["peak_mib"] = peak


def _system_ram() -> tuple[float, float]:
    vm = psutil.virtual_memory()
    return vm.used / (1024 * 1024), vm.available / (1024 * 1024)


# ---------------------------------------------------------------------
# Mode 1: load-per-call  (spawn llama-cli fresh, run once, exit)
# ---------------------------------------------------------------------

def run_load_per_call(prompt_id: str, prompt: str) -> dict:
    cmd = [
        LLAMA_CLI_BIN, "-m", MODEL_PATH,
        "-p", prompt,
        "-n", str(MAX_TOKENS),
        "-t", str(N_THREADS),
    ]
    ram_before_used, ram_before_avail = _system_ram()
    t0 = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

    sample_out = {}
    stop_event = threading.Event()
    ps_proc = psutil.Process(proc.pid)
    sampler = threading.Thread(target=_sample_peak_rss, args=(ps_proc, stop_event, sample_out))
    sampler.start()

    stdout, _ = proc.communicate()
    stop_event.set()
    sampler.join()
    elapsed = time.perf_counter() - t0

    ram_after_used, ram_after_avail = _system_ram()

    return {
        "mode": "load-per-call",
        "prompt_id": prompt_id,
        "latency_s": round(elapsed, 2),
        "peak_rss_mib": round(sample_out.get("peak_mib", 0), 2),
        "ram_used_before_mib": round(ram_before_used, 1),
        "ram_avail_before_mib": round(ram_before_avail, 1),
        "ram_used_after_mib": round(ram_after_used, 1),
        "ram_avail_after_mib": round(ram_after_avail, 1),
        # llama-cli output may include the prompt itself depending on your
        # build's flags -- read manually, this is for faithfulness review
        # not exact parsing.
        "output": stdout.strip().replace("\n", " ")[:300],
    }


# ---------------------------------------------------------------------
# Mode 2: keep-loaded  (one llama-server stays resident across all calls)
# ---------------------------------------------------------------------

def start_server() -> subprocess.Popen:
    cmd = [
        LLAMA_SERVER_BIN, "-m", MODEL_PATH,
        "--host", SERVER_HOST, "--port", str(SERVER_PORT),
        "-t", str(N_THREADS),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://{SERVER_HOST}:{SERVER_PORT}/health"
    for _ in range(100):
        try:
            r = requests.get(url, timeout=1)
            if r.status_code == 200:
                break
        except requests.RequestException:
            pass
        time.sleep(0.3)
    return proc


def run_keep_loaded(server_proc: subprocess.Popen, prompt_id: str, prompt: str) -> dict:
    ram_before_used, ram_before_avail = _system_ram()

    sample_out = {}
    stop_event = threading.Event()
    ps_proc = psutil.Process(server_proc.pid)
    sampler = threading.Thread(target=_sample_peak_rss, args=(ps_proc, stop_event, sample_out))
    sampler.start()

    t0 = time.perf_counter()
    resp = requests.post(
        f"http://{SERVER_HOST}:{SERVER_PORT}/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": MAX_TOKENS,
            "temperature": 0.2,
        },
        timeout=60,
    )
    elapsed = time.perf_counter() - t0

    stop_event.set()
    sampler.join()

    ram_after_used, ram_after_avail = _system_ram()
    try:
        text = resp.json()["choices"][0]["message"]["content"]
    except Exception:
        text = resp.text

    return {
        "mode": "keep-loaded",
        "prompt_id": prompt_id,
        "latency_s": round(elapsed, 2),
        "peak_rss_mib": round(sample_out.get("peak_mib", 0), 2),
        "ram_used_before_mib": round(ram_before_used, 1),
        "ram_avail_before_mib": round(ram_before_avail, 1),
        "ram_used_after_mib": round(ram_after_used, 1),
        "ram_avail_after_mib": round(ram_after_avail, 1),
        "output": text.strip().replace("\n", " ")[:300],
    }


# ---------------------------------------------------------------------
# CSV logging + summary
# ---------------------------------------------------------------------

FIELDNAMES = [
    "timestamp", "mode", "prompt_id", "latency_s", "peak_rss_mib",
    "ram_used_before_mib", "ram_avail_before_mib",
    "ram_used_after_mib", "ram_avail_after_mib",
    "faithfulness_flag", "output",
]


def log_row(row: dict):
    row["timestamp"] = datetime.now(timezone.utc).isoformat()
    file_exists = Path(RESULTS_CSV).exists()
    with open(RESULTS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def print_summary(rows: list[dict], mode: str):
    latencies = [r["latency_s"] for r in rows]
    peaks = [r["peak_rss_mib"] for r in rows]
    print(f"\n=== {mode} summary ({len(rows)} calls) ===")
    print(f"  latency  s  : mean={statistics.mean(latencies):.2f}  "
          f"median={statistics.median(latencies):.2f}  max={max(latencies):.2f}")
    print(f"  peak RSS MiB: mean={statistics.mean(peaks):.1f}  max={max(peaks):.1f}")


# ---------------------------------------------------------------------
# Faithfulness heuristic — pre-sorts candidates worth a manual look.
# This does NOT replace manual review of the "output" column.
# ---------------------------------------------------------------------

_HEDGE_WORDS = ("i think", "maybe", "probably", "i'm not sure", "might have",
                "i believe", "possibly", "not sure")


def flag_faithfulness(output: str) -> str:
    low = output.lower()
    if not output:
        return "EMPTY"
    if any(w in low for w in _HEDGE_WORDS):
        return "HEDGED"          # tool succeeded but model sounds unsure
    if len(output.split()) > 40:
        return "TOO_LONG"        # asked for one short sentence
    return "review_manually"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["load-per-call", "keep-loaded", "both"], default="both")
    parser.add_argument("--n", type=int, default=len(PROMPTS),
                        help="Number of calls to run (cycles through PROMPTS if larger)")
    args = parser.parse_args()

    prompts_to_run = [PROMPTS[i % len(PROMPTS)] for i in range(args.n)]

    if args.mode in ("load-per-call", "both"):
        print(f"\n--- Running {len(prompts_to_run)} calls: load-per-call ---")
        rows = []
        for prompt_id, prompt in prompts_to_run:
            row = run_load_per_call(prompt_id, prompt)
            row["faithfulness_flag"] = flag_faithfulness(row["output"])
            log_row(row)
            rows.append(row)
            print(f"  [{prompt_id}] {row['latency_s']}s  {row['peak_rss_mib']}MiB  "
                  f"flag={row['faithfulness_flag']}  -> {row['output'][:80]}")
        print_summary(rows, "load-per-call")

    if args.mode in ("keep-loaded", "both"):
        print("\n--- Starting llama-server for keep-loaded mode ---")
        server_proc = start_server()
        try:
            print(f"--- Running {len(prompts_to_run)} calls: keep-loaded ---")
            rows = []
            for prompt_id, prompt in prompts_to_run:
                row = run_keep_loaded(server_proc, prompt_id, prompt)
                row["faithfulness_flag"] = flag_faithfulness(row["output"])
                log_row(row)
                rows.append(row)
                print(f"  [{prompt_id}] {row['latency_s']}s  {row['peak_rss_mib']}MiB  "
                      f"flag={row['faithfulness_flag']}  -> {row['output'][:80]}")
            print_summary(rows, "keep-loaded")
        finally:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()

    print(f"\nAll results appended to {RESULTS_CSV}")
    print("Faithfulness flags are a rough pre-filter only -- read each 'output' "
          "column yourself and score whether it accurately reflects the tool "
          "result (no fabricated numbers, no hedging, no contradicting the "
          "actual outcome).")


if __name__ == "__main__":
    main()