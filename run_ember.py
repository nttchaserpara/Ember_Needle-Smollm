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
import threading
import time
from copy import deepcopy

import psutil

_PROCESS = psutil.Process(os.getpid())
_PROGRAM_STARTED = time.perf_counter()

sys.path.insert(0, ".")

from needle_router import route_and_execute
from emberos.tools import ToolRegistry, ToolResult
from emberos.benchmark import RequestMemorySampler
from emberos.responses import format_tool_response
from emberos.config import ROOT_DIR
from emberos.memory import ConversationMemory, memory_command
from emberos.replies import ReplyRenderer, original_response


def _format_multi_tool_response(result):
    """Render a multi_tool_call result without SmolLM -- natural-reply
    generation for multi-result outcomes is deferred to a later pass.
    """
    lines = []
    for item in result.get("results", []):
        if item.get("route") == "skipped":
            lines.append(f"[SKIPPED] {item.get('tool')} (an earlier step failed)")
            continue
        lines.append(format_tool_response(item))
    if result.get("chain_undo_available"):
        lines.append("(This whole chain can be undone with 'undo'.)")
    else:
        lines.append("(Nothing in this chain can be undone.)")
    return "\n".join(lines)


DESTRUCTIVE_TOOLS = frozenset({
    "delete_file",
    "move_file",
    "rename_file",
    "organize_folder",
    "clear_completed_tasks",
    "shutdown_system",
    "restart_system",
    "kill_process",
    "sleep_system",
    "lock_screen",
})

CONFIRMATION_WORDS = frozenset({
    "yes", "yeah", "yep", "yup", "sure", "do it", "confirm",
    "ok", "oke", "iya", "lakukan",
})

_PENDING_EMPTY = {"tool": None, "params": None, "calls": None, "message": None}
_pending_confirmation = dict(_PENDING_EMPTY)
_pending_lock = threading.RLock()


def is_confirmation(text: str) -> bool:
    """Return whether *text* is one of the exact accepted confirmations."""
    return text.strip().casefold() in CONFIRMATION_WORDS


def _build_confirm_message(tool_name: str, params: dict) -> str:
    messages = {
        "delete_file": f"WARNING: permanently delete '{params.get('path', '?')}'? (yes/no)",
        "move_file": f"WARNING: move '{params.get('src', '?')}' to '{params.get('dst', '?')}'? (yes/no)",
        "rename_file": f"WARNING: rename '{params.get('path', '?')}' to '{params.get('new_name', '?')}'? (yes/no)",
        "organize_folder": f"WARNING: move files in '{params.get('folder', '?')}'? (yes/no)",
        "clear_completed_tasks": "WARNING: delete all completed tasks? (yes/no)",
        "shutdown_system": "WARNING: shut down the computer? (yes/no)",
        "restart_system": "WARNING: restart the computer? (yes/no)",
        "kill_process": f"WARNING: kill process '{params.get('target', '?')}'? (yes/no)",
        "sleep_system": "WARNING: put the computer to sleep? (yes/no)",
        "lock_screen": "WARNING: lock the screen? (yes/no)",
    }
    return messages.get(
        tool_name,
        f"WARNING: run '{tool_name}' with {params}? (yes/no)",
    )


def _build_chain_confirm_message(calls: list[dict]) -> str:
    names = ", ".join(call.get("name", "unknown") for call in calls)
    return f"WARNING: this multi-step request will run: {names}. Continue? (yes/no)"


def _set_pending_confirmation(*, tool: str | None = None, params: dict | None = None,
                              calls: list[dict] | None = None, message: str) -> None:
    global _pending_confirmation
    with _pending_lock:
        _pending_confirmation = {
            "tool": tool,
            "params": deepcopy(params) if params is not None else None,
            "calls": deepcopy(calls) if calls is not None else None,
            "message": message,
        }


def _take_pending_confirmation() -> dict | None:
    global _pending_confirmation
    with _pending_lock:
        if _pending_confirmation["tool"] is None and not _pending_confirmation["calls"]:
            return None
        pending = deepcopy(_pending_confirmation)
        _pending_confirmation = dict(_PENDING_EMPTY)
        return pending


def _peek_pending_confirmation() -> dict | None:
    with _pending_lock:
        if _pending_confirmation["tool"] is None and not _pending_confirmation["calls"]:
            return None
        return deepcopy(_pending_confirmation)


def _confirmation_result(pending: dict) -> dict:
    calls = pending.get("calls") or []
    tool = pending.get("tool") or ("+".join(c.get("name", "") for c in calls) if calls else None)
    arguments = pending.get("params") if pending.get("tool") else calls
    message = pending.get("message") or "Confirmation required."
    return {
        "route": "confirmation_required",
        "tool": tool,
        "arguments": arguments,
        "success": False,
        "status": "confirmation_required",
        "response": message,
        "display_response": message,
    }


def _tool_call_result(tool_name: str, params: dict, exec_result: ToolResult,
                      confidence: float | None = None) -> dict:
    result = {
        "route": "tool_call",
        "tool": tool_name,
        "arguments": params,
        "success": exec_result.success,
        "result": exec_result.result,
        "error": exec_result.error,
        "status": exec_result.status,
        "message": exec_result.message,
        "data": exec_result.data,
    }
    if confidence is not None:
        result["confidence"] = confidence
    return result


def _execute_confirmed(registry, pending: dict) -> dict:
    calls = pending.get("calls")
    if calls:
        execute_chain = getattr(registry, "execute_confirmed_chain", None)
        if execute_chain is None:
            results, composite = registry.execute_tool_chain(calls)
        else:
            results, composite = execute_chain(calls)
        registry.install_chain_undo(composite)
        return {
            "route": "multi_tool_call",
            "results": results,
            "calls": calls,
            "confidence": None,
            "chain_undo_available": composite is not None,
            "truncated": False,
        }

    tool_name = pending["tool"]
    params = pending.get("params") or {}
    execute_one = getattr(registry, "execute_confirmed", None)
    exec_result = (execute_one(tool_name, params)
                   if execute_one is not None
                   else registry.execute_tool(tool_name, params))
    return _tool_call_result(tool_name, params, exec_result)


class _ConfirmationGateRegistry:
    """Delegate registry operations while holding destructive executions."""

    def __init__(self, registry):
        self._registry = registry

    def __getattr__(self, name):
        return getattr(self._registry, name)

    def execute_tool(self, name: str, params: dict):
        if name in DESTRUCTIVE_TOOLS:
            message = _build_confirm_message(name, params)
            _set_pending_confirmation(tool=name, params=params, message=message)
            return ToolResult(success=False, error=message,
                              status="confirmation_required", message=message)
        return self._registry.execute_tool(name, params)

    def execute_tool_chain(self, calls: list[dict]):
        destructive = [call for call in calls if call.get("name") in DESTRUCTIVE_TOOLS]
        if destructive:
            message = _build_chain_confirm_message(calls)
            _set_pending_confirmation(calls=calls, message=message)
            results = []
            for call in calls:
                results.append({
                    "route": "tool_call",
                    "tool": call.get("name"),
                    "arguments": call.get("arguments") or {},
                    "success": False,
                    "result": None,
                    "error": message,
                    "status": "confirmation_required",
                    "message": message,
                    "data": None,
                })
            return results, None
        return self._registry.execute_tool_chain(calls)


def _present_result(result, query, memory, reply_renderer, *, render_reply=True):
    """Attach display/reply metadata and persist the completed turn."""
    if result.get("route") == "multi_tool_call":
        display_response = _format_multi_tool_response(result)
        result["display_response"] = display_response
        result["reply"] = {"text": display_response, "original": display_response,
                           "candidate": "", "source": "original",
                           "reason": "multi_step_not_generated",
                           "elapsed_seconds": 0.0, "generation": None,
                           "history_turn_ids": []}
        result["tool"] = "+".join(
            item["tool"] for item in result.get("results", [])
            if item.get("route") == "tool_call" and item.get("tool")
        )
        result["arguments"] = result.get("calls")
    elif render_reply:
        renderer = reply_renderer if reply_renderer is not None else ReplyRenderer()
        reply = renderer.render(result, query=query, memory=memory)
        result["display_response"] = reply["text"]
        result["reply"] = reply
    else:
        display_response = result.get("display_response") or result.get("response") or ""
        result["display_response"] = display_response
        result["reply"] = {"text": display_response, "original": display_response,
                           "candidate": "", "source": "original",
                           "reason": "direct_response", "elapsed_seconds": 0.0,
                           "generation": None, "history_turn_ids": []}

    if memory is not None:
        response = result.get("display_response") or original_response(result)
        try:
            memory.record(query, response, result)
        except (OSError, sqlite3.Error, ValueError) as exc:
            print(f"[memory] This turn could not be saved: {exc}")
    return result


def handle_request(query, registry, memory=None, *, reply_renderer=None, multi_step_mode=False):
    """Keep persistence failures separate from the already executed action."""
    pending = _take_pending_confirmation()
    if pending is not None:
        if is_confirmation(query):
            result = _execute_confirmed(registry, pending)
            return _present_result(result, query, memory, reply_renderer)
        result = {
            "route": "no_action",
            "success": False,
            "status": "cancelled",
            "response": "Action cancelled.",
            "display_response": "Action cancelled.",
        }
        return _present_result(result, query, memory, reply_renderer, render_reply=False)

    try:
        memory_response = memory_command(query, memory)
    except (OSError, sqlite3.Error, ValueError) as exc:
        memory_response = f"Conversation memory is unavailable: {exc}"
    if memory_response is not None:
        # Do not record control commands: clearing must leave an empty store,
        # and displaying history must not recursively fill the history.
        return {"route": "memory_view", "response": memory_response}
    try:
        gated_registry = _ConfirmationGateRegistry(registry)
        if multi_step_mode:
            result = route_and_execute(query, gated_registry, multi_step_mode=True)
        else:
            # Keep the default call shape compatible with lightweight test and
            # embedding stubs that implement the original two-argument API.
            result = route_and_execute(query, gated_registry)
    except Exception as exc:
        result = {"route": "error", "response": f"Request failed: {exc}",
                  "status": "error", "success": False}
    pending = _peek_pending_confirmation()
    if pending is not None:
        result = _confirmation_result(pending)
        return _present_result(result, query, memory, reply_renderer, render_reply=False)
    return _present_result(result, query, memory, reply_renderer)


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
    multistep_enabled = True
    print("EmberOS (Needle OS Agent) -- type 'exit' to quit")
    _print_benchmark("startup", elapsed=time.perf_counter() - _PROGRAM_STARTED)
    print("[mode] SmolLM2 Q4 uses llama.cpp for summaries and short replies. "
          + ("Persistent worker enabled." if os.environ.get("EMBER_LLM_PERSISTENT", "0") == "1"
             else "The worker unloads after each generation job."))
    print("[memory] Local conversation history enabled (up to 1,000 turns). Type /memory for recent history."
          if memory is not None else "[memory] Conversation history disabled or unavailable.")
    print("[multistep] OFF by desfault. Type /multistep on to allow allowlisted multi-action chains.")
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
        if query.lower() == "/multistep on":
            multistep_enabled = True
            print("[multistep] ON -- allowlisted multi-action chains enabled.")
            continue
        if query.lower() == "/multistep off":
            multistep_enabled = False
            print("[multistep] OFF -- back to one action per request.")
            continue
        if query.lower() == "/multistep":
            print(f"[multistep] currently {'ON' if multistep_enabled else 'OFF'}.")
            continue

        rss_before, _ = _memory_snapshot()
        started = time.perf_counter()
        sample = RequestMemorySampler(_PROCESS)
        try:
            with sample:
                result = handle_request(query, registry, memory, reply_renderer=reply_renderer,
                                        multi_step_mode=multistep_enabled)
        except Exception as e:
            print(f"[error] {e}")
            _print_benchmark("failed request", time.perf_counter() - started, rss_before, sample)
            continue

        if result["route"] == "tool_call":
            print(result.get("display_response") or format_tool_response(result))
            confidence = result.get("confidence")
            confidence_text = f"{confidence:.2f}" if confidence is not None else "unknown"
            print(f"  [tool={result['tool']} | confidence={confidence_text}]")
        elif result["route"] == "multi_tool_call":
            print(result.get("display_response"))
            ran = sum(1 for item in result.get("results", []) if item.get("route") != "skipped")
            total = len(result.get("calls", []))
            confidence = result.get("confidence")
            confidence_text = f"{confidence:.2f}" if confidence is not None else "unknown"
            print(f"  [multi-step: {ran}/{total} steps ran | confidence={confidence_text}]")
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
