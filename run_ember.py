"""
run_ember.py

Entry point beneran -- ketik query kamu sendiri, bukan list query yang
udah ditentuin kayak di experiments/. Ini yang bakal jadi "produk" jalan.

Jalanin dari root Ember_tools&llm/:
    python run_ember.py
"""

import os
import sqlite3
import sys
import time

import psutil

_PROCESS = psutil.Process(os.getpid())
_PROGRAM_STARTED = time.perf_counter()

sys.path.insert(0, ".")

from needle_router import route_and_execute
from emberos.tools import ToolRegistry
from emberos.benchmark import RequestMemorySampler
from emberos.responses import format_tool_response
from emberos.config import ROOT_DIR
from emberos.memory import ConversationMemory, memory_command
from emberos.replies import ReplyRenderer, original_response


def handle_request(query, registry, memory=None, *, reply_renderer=None):
    """Keep persistence failures separate from the already executed action."""
    try:
        memory_response = memory_command(query, memory)
    except (OSError, sqlite3.Error, ValueError) as exc:
        memory_response = f"Conversation memory is unavailable: {exc}"
    if memory_response is not None:
        # Do not record control commands: clearing must leave an empty store,
        # and displaying history must not recursively fill the history.
        return {"route": "memory_view", "response": memory_response}
    try:
        result = route_and_execute(query, registry)
    except Exception as exc:
        result = {"route": "error", "response": f"Request failed: {exc}",
                  "status": "error", "success": False}
    renderer = reply_renderer if reply_renderer is not None else ReplyRenderer()
    reply = renderer.render(result, query=query, memory=memory)
    result["display_response"] = reply["text"]
    result["reply"] = reply
    if memory is not None:
        response = result.get("display_response") or original_response(result)
        try:
            memory.record(query, response, result)
        except (OSError, sqlite3.Error, ValueError) as exc:
            print(f"[memory] This turn could not be saved: {exc}")
    return result


def _format_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _memory_snapshot() -> tuple[int, object]:
    return _PROCESS.memory_info().rss, psutil.virtual_memory()


def _print_benchmark(label: str, elapsed: float | None = None,
                     rss_before: int | None = None,
                     sample: RequestMemorySampler | None = None) -> None:
    rss, memory = _memory_snapshot()
    parts = [
        f"Ember RSS now {_format_bytes(rss)}",
        f"whole-system RAM {_format_bytes(memory.used)} used of {_format_bytes(memory.total)} "
        f"({_format_bytes(memory.available)} available)",
    ]
    if elapsed is not None:
        parts.insert(0, f"{elapsed:.2f}s")
    if sample is not None:
        parts.append(
            f"sampled peak RSS (Ember + children) {sample.peak_total / 2**20:.1f} MiB "
            f"(Ember {sample.parent_at_peak / 2**20:.1f} + "
            f"children {sample.children_at_peak / 2**20:.1f} MiB at peak)"
        )
    if rss_before is not None:
        delta = rss - rss_before
        sign = "+" if delta >= 0 else "-"
        parts.append(f"process delta {sign}{_format_bytes(abs(delta))}")
    print(f"[benchmark | {label}] " + " | ".join(parts))


def main():
    memory = None
    if os.environ.get("EMBER_MEMORY", "1") != "0":
        try:
            memory = ConversationMemory(ROOT_DIR / "data" / "conversation.sqlite3")
        except (OSError, sqlite3.Error, ValueError) as exc:
            print(f"[memory] Conversation memory is unavailable: {exc}")
    registry = ToolRegistry(memory=memory)
    reply_renderer = ReplyRenderer()
    print("EmberOS (Needle OS Agent) -- type 'exit' to quit")
    _print_benchmark("startup", elapsed=time.perf_counter() - _PROGRAM_STARTED)
    print("[mode] SmolLM2 Q4 uses llama.cpp for summaries and short replies. "
          + ("Persistent worker enabled." if os.environ.get("EMBER_LLM_PERSISTENT", "0") == "1"
             else "The worker unloads after each generation job."))
    print("[memory] Local conversation history enabled (up to 1,000 turns). Type /memory for recent history."
          if memory is not None else "[memory] Conversation history disabled or unavailable.")
    print("-" * 50)

    while True:
        try:
            query = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not query:
            continue
        if query.lower() in ("exit", "quit"):
            break

        rss_before, _ = _memory_snapshot()
        started = time.perf_counter()
        sample = RequestMemorySampler(_PROCESS)
        try:
            with sample:
                result = handle_request(query, registry, memory, reply_renderer=reply_renderer)
        except Exception as e:
            print(f"[error] {e}")
            _print_benchmark("failed request", time.perf_counter() - started, rss_before, sample)
            continue

        if result["route"] == "tool_call":
            print(result.get("display_response") or format_tool_response(result))
            confidence = result.get("confidence")
            confidence_text = f"{confidence:.2f}" if confidence is not None else "unknown"
            print(f"  [tool={result['tool']} | confidence={confidence_text}]")
        elif result["route"] == "memory_view":
            print(result.get("display_response") or result["response"])
        else:
            conf = result.get("needle_confidence")
            conf_str = f"{conf:.2f}" if conf is not None else "None"
            print(f"[route={result['route']} | Needle confidence={conf_str}]")
            print(result.get("display_response") or result["response"])

        _print_benchmark("request", time.perf_counter() - started, rss_before, sample)


if __name__ == "__main__":
    main()
