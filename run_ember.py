"""
run_ember.py

Entry point beneran -- ketik query kamu sendiri, bukan list query yang
sudah ditentukan sebelumnya.

Jalanin dari root Ember_tools&llm/:
    python run_ember.py
"""

import os
import re
import sqlite3
import sys
import threading
import time
from copy import deepcopy
from pathlib import Path

import psutil

_PROCESS = psutil.Process(os.getpid())
_PROGRAM_STARTED = time.perf_counter()

sys.path.insert(0, str(Path(__file__).resolve().parent))

from needle_router import route_and_execute
from emberos.tools import ToolRegistry, ToolResult
from emberos.benchmark import RequestMemorySampler
from emberos.responses import format_tool_response
from emberos.config import ROOT_DIR, data_dir
from emberos.memory import ConversationMemory, memory_command
from emberos.replies import ReplyRenderer, original_response


def _format_multi_tool_response(result):
    """Render a multi_tool_call result without SmolLM -- natural-reply
    generation for multi-result outcomes is deferred to a later pass.
    """
    lines = []
    for item in result.get("results", []):
        if item.get("route") == "skipped":
            lines.append("I skipped one step because an earlier step failed.")
            continue
        lines.append(format_tool_response(item))
    if result.get("chain_undo_available"):
        lines.append("You can undo this sequence.")
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

_PENDING_EMPTY = {"tool": None, "params": None, "calls": None, "message": None,
                  "file_snapshot": None}
_pending_confirmations = {}
_pending_file_references = {}
_recent_file_context = {}
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
                              calls: list[dict] | None = None, message: str,
                              session_id: str = "default",
                              file_snapshot: dict | None = None) -> None:
    with _pending_lock:
        _pending_confirmations[session_id] = {
            "tool": tool,
            "params": deepcopy(params) if params is not None else None,
            "calls": deepcopy(calls) if calls is not None else None,
            "message": message,
            "file_snapshot": deepcopy(file_snapshot),
        }


def _take_pending_confirmation(session_id: str = "default") -> dict | None:
    with _pending_lock:
        current = _pending_confirmations.get(session_id, _PENDING_EMPTY)
        if current["tool"] is None and not current["calls"]:
            return None
        return deepcopy(_pending_confirmations.pop(session_id))


def _peek_pending_confirmation(session_id: str = "default") -> dict | None:
    with _pending_lock:
        pending = _pending_confirmations.get(session_id)
        if pending is None or (pending["tool"] is None and not pending["calls"]):
            return None
        return deepcopy(pending)


def _set_pending_file_reference(*, action: str, params: dict, candidates: list[str],
                                message: str, session_id: str) -> None:
    with _pending_lock:
        _pending_file_references[session_id] = {
            "action": action,
            "params": deepcopy(params),
            "candidates": list(candidates),
            "message": message,
        }


def _take_pending_file_reference(session_id: str) -> dict | None:
    with _pending_lock:
        pending = _pending_file_references.pop(session_id, None)
        return deepcopy(pending) if pending is not None else None


def _peek_pending_file_reference(session_id: str) -> dict | None:
    with _pending_lock:
        pending = _pending_file_references.get(session_id)
        return deepcopy(pending) if pending is not None else None


def _set_recent_file_context(session_id: str, path: str) -> None:
    with _pending_lock:
        _recent_file_context[session_id] = str(Path(path).expanduser().resolve())


def _get_recent_file_context(session_id: str) -> str | None:
    with _pending_lock:
        path = _recent_file_context.get(session_id)
        return str(path) if path else None


def _clear_recent_file_context(session_id: str) -> None:
    with _pending_lock:
        _recent_file_context.pop(session_id, None)


_CONTEXTUAL_FILE_REFERENCE_RE = re.compile(
    r"\s*(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?"
    r"(?P<verb>read|summari[sz]e|delete|remove)\s+"
    r"(?:(?:the|same|last|previous)\s+)?"
    r"(?P<reference>it|this|that|this file|that file|same file|last one|previous one)"
    r"(?:\s+please)?\s*[.!?]?\s*",
    re.IGNORECASE,
)


def _contextual_file_action(query: str) -> str | None:
    match = _CONTEXTUAL_FILE_REFERENCE_RE.fullmatch(query)
    if not match:
        return None
    verb = match.group("verb").casefold()
    if verb == "read":
        return "read_file"
    if verb.startswith("summari"):
        return "summarize_file"
    return "delete_file"


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
    if tool_name == "delete_file" and pending.get("file_snapshot") is not None:
        current = _file_snapshot(params.get("path", ""))
        if current != pending["file_snapshot"]:
            return _tool_call_result(
                tool_name, params,
                ToolResult(
                    success=False,
                    error="The file changed or disappeared after confirmation was requested. No file was deleted; please request the action again.",
                    status="error",
                ),
            )
    execute_one = getattr(registry, "execute_confirmed", None)
    exec_result = (execute_one(tool_name, params)
                   if execute_one is not None
                   else registry.execute_tool(tool_name, params))
    return _tool_call_result(tool_name, params, exec_result)


_FILE_REFERENCE_TOOLS = frozenset({"read_file", "summarize_file", "delete_file"})
_FILE_SELECTION_CANCEL = frozenset({"cancel", "cancel it", "never mind", "nevermind", "forget it"})
_FILE_SELECTION_WAIT = frozenset({"yes", "yeah", "yep", "no", "nope", "ok", "okay"})
_ORDINAL_CHOICES = {
    "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "sixth": 6, "6th": 6,
    "seventh": 7, "7th": 7, "eighth": 8, "8th": 8,
}


def _resolve_file_reference(reference: str) -> list[str]:
    """Resolve an exact path, or return bounded filename matches under home."""
    from use_cases.file_ops import search_file_references

    raw = str(reference or "").strip().strip("'\" ")
    if not raw:
        return []
    supplied = Path(raw).expanduser()
    direct_paths = [supplied]
    if not supplied.is_absolute():
        direct_paths.extend((ROOT_DIR / supplied, Path.home() / supplied))
    existing = []
    for candidate in direct_paths:
        try:
            if candidate.exists():
                resolved = str(candidate.resolve())
                if resolved not in existing:
                    existing.append(resolved)
        except OSError:
            continue
    if existing:
        return existing

    search_root = Path.home()
    if not supplied.is_absolute() and supplied.parent != Path("."):
        for base in (ROOT_DIR, Path.home()):
            possible_root = (base / supplied.parent).resolve()
            if possible_root.is_dir():
                search_root = possible_root
                break
    return search_file_references(supplied.name, str(search_root))


def _file_snapshot(path: str) -> dict | None:
    """Capture an exact regular-file identity for a pending delete confirmation."""
    try:
        target = Path(path).expanduser()
        if target.is_symlink() or not target.is_file():
            return None
        resolved = target.resolve(strict=True)
        stat = resolved.stat()
        return {
            "path": str(resolved),
            "device": stat.st_dev,
            "inode": stat.st_ino,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "ctime_ns": stat.st_ctime_ns,
        }
    except OSError:
        return None


def _file_reference_question(action: str, reference: str, candidates: list[str]) -> str:
    rows = [
        f"{index}. {Path(path).name} — {Path(path).parent}"
        for index, path in enumerate(candidates, 1)
    ]
    verb = {"read_file": "read", "summarize_file": "summarize", "delete_file": "delete"}.get(
        action, "use"
    )
    return (
        f"I found several files matching '{reference}'. Which one should I {verb}? "
        "Reply with a filename, folder, or list number. Nothing has been changed.\n"
        + "\n".join(rows)
    )


def _select_file_candidate(text: str, pending: dict) -> tuple[str | None, bool]:
    query = " ".join(text.casefold().split()).strip(" .!?'\"")
    candidates = pending["candidates"]
    ordinal = None
    if query.isdigit():
        ordinal = int(query)
    else:
        for word, number in _ORDINAL_CHOICES.items():
            if re.search(rf"\b{re.escape(word)}\b", query):
                ordinal = number
                break
        if ordinal is None:
            numbered = re.fullmatch(r"(?:file|option|number|#)\s*#?(\d+)", query)
            if numbered:
                ordinal = int(numbered.group(1))
    if ordinal is not None:
        return (candidates[ordinal - 1], False) if 1 <= ordinal <= len(candidates) else (None, True)

    scored = []
    for candidate in candidates:
        path = Path(candidate)
        filename = path.name.casefold()
        folder = path.parent.name.casefold()
        suffix = path.suffix.casefold().lstrip(".")
        stem = path.stem.casefold()
        score = 0
        if str(path).casefold() in query:
            score = 100
        elif filename and filename in query:
            score = 50
        if suffix and re.search(rf"\b{re.escape(suffix)}\b", query):
            score += 5
        if folder and re.search(rf"\b{re.escape(folder)}\b", query):
            score += 20
        words = [word for word in re.findall(r"[a-z0-9]+", stem) if len(word) > 1]
        query_words = set(re.findall(r"[a-z0-9]+", query))
        if words and all(word in query_words for word in words):
            score += 25
        if score:
            scored.append((score, candidate))
    if not scored:
        return None, False
    highest = max(score for score, _ in scored)
    matches = [candidate for score, candidate in scored if score == highest]
    if len(matches) == 1:
        return matches[0], False
    return None, True


def _file_clarification_result(pending: dict) -> dict:
    return {
        "route": "file_clarification",
        "tool": pending["action"],
        "arguments": pending["params"],
        "success": False,
        "status": "clarification_required",
        "response": pending["message"],
        "display_response": pending["message"],
    }


def _execute_selected_file(registry, pending: dict, selected_path: str,
                           query: str, memory, reply_renderer, session_id: str):
    action = pending["action"]
    params = dict(pending["params"])
    params["path"] = selected_path
    if action == "delete_file":
        outcome = registry.execute_tool(action, params)
        if _peek_pending_confirmation(session_id) is not None:
            result = _confirmation_result(_peek_pending_confirmation(session_id))
        else:
            result = _tool_call_result(action, params, outcome)
        return _present_result(result, query, memory, reply_renderer,
                               render_reply=result.get("route") != "confirmation_required")
    outcome = registry.execute_tool(action, params)
    result = _tool_call_result(action, params, outcome)
    return _present_result(result, query, memory, reply_renderer)


class _ConfirmationGateRegistry:
    """Delegate registry operations while holding destructive executions."""

    def __init__(self, registry, *, session_id: str = "default", query: str = ""):
        self._registry = registry
        self._session_id = session_id
        self._query = query

    def __getattr__(self, name):
        return getattr(self._registry, name)

    def execute_tool(self, name: str, params: dict):
        params = dict(params)
        if name in _FILE_REFERENCE_TOOLS:
            reference = str(params.get("path", ""))
            candidates = _resolve_file_reference(reference)
            if not candidates:
                return ToolResult(
                    success=False,
                    error=f"I couldn't find a file matching '{reference}' in your home folder. No file was changed.",
                    status="error",
                )
            if len(candidates) > 1:
                message = _file_reference_question(name, reference, candidates)
                _set_pending_file_reference(
                    action=name, params=params, candidates=candidates,
                    message=message, session_id=self._session_id,
                )
                return ToolResult(success=False, error=message, status="clarification_required",
                                  message=message)
            params["path"] = candidates[0]
        if name in DESTRUCTIVE_TOOLS:
            file_snapshot = None
            if name == "delete_file":
                file_snapshot = _file_snapshot(params.get("path", ""))
                if file_snapshot is None:
                    return ToolResult(
                        success=False,
                        error="I can't safely confirm this deletion because the target is not a regular file. No file was changed.",
                        status="error",
                    )
            message = _build_confirm_message(name, params)
            _set_pending_confirmation(tool=name, params=params, message=message,
                                      session_id=self._session_id,
                                      file_snapshot=file_snapshot)
            return ToolResult(success=False, error=message,
                              status="confirmation_required", message=message)
        outcome = self._registry.execute_tool(name, params)
        if name in {"read_file", "summarize_file"}:
            if getattr(outcome, "success", False):
                _set_recent_file_context(self._session_id, params.get("path", ""))
            else:
                _clear_recent_file_context(self._session_id)
        return outcome

    def execute_tool_chain(self, calls: list[dict]):
        destructive = [call for call in calls if call.get("name") in DESTRUCTIVE_TOOLS]
        if any(call.get("name") in _FILE_REFERENCE_TOOLS for call in calls):
            raise ValueError("Resolve file references with a single file request before using multi-step mode.")
        if destructive:
            message = _build_chain_confirm_message(calls)
            _set_pending_confirmation(calls=calls, message=message,
                                      session_id=self._session_id)
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


def handle_request(query, registry, memory=None, *, reply_renderer=None,
                   multi_step_mode=False, session_id="default"):
    """Keep persistence failures separate from the already executed action."""
    session_id = str(session_id or "default")
    pending = _take_pending_confirmation(session_id)
    if pending is not None:
        if is_confirmation(query):
            result = _execute_confirmed(registry, pending)
            if pending.get("tool") == "delete_file":
                _clear_recent_file_context(session_id)
            return _present_result(result, query, memory, reply_renderer)
        result = {
            "route": "no_action",
            "success": False,
            "status": "cancelled",
            "response": "Action cancelled.",
            "display_response": "Action cancelled.",
        }
        return _present_result(result, query, memory, reply_renderer, render_reply=False)

    pending_file = _take_pending_file_reference(session_id)
    if pending_file is not None:
        selected_path, ambiguous = _select_file_candidate(query, pending_file)
        if selected_path:
            gated_registry = _ConfirmationGateRegistry(
                registry, session_id=session_id, query=query
            )
            return _execute_selected_file(
                gated_registry, pending_file, selected_path, query, memory,
                reply_renderer, session_id,
            )
        cleaned = " ".join(query.casefold().split()).strip(" .!?")
        if cleaned in _FILE_SELECTION_CANCEL:
            result = {
                "route": "no_action", "success": False, "status": "cancelled",
                "response": "File selection cancelled. No file was changed.",
            }
            return _present_result(result, query, memory, reply_renderer, render_reply=False)
        if ambiguous or cleaned in _FILE_SELECTION_WAIT:
            _set_pending_file_reference(
                action=pending_file["action"], params=pending_file["params"],
                candidates=pending_file["candidates"], message=pending_file["message"],
                session_id=session_id,
            )
            return _present_result(_file_clarification_result(pending_file), query,
                                   memory, reply_renderer, render_reply=False)
        # A different request replaces the pending question; it must not be
        # swallowed as a file choice. Continue routing this text normally.
        _clear_recent_file_context(session_id)

    contextual_action = _contextual_file_action(query)
    if contextual_action:
        recent_path = _get_recent_file_context(session_id)
        if recent_path is None:
            result = {
                "route": "no_action",
                "success": False,
                "status": "unresolved_reference",
                "response": (
                    "I don't know which file that refers to. Read or select a file first, "
                    "or name the file you mean. No file was changed."
                ),
            }
            return _present_result(result, query, memory, reply_renderer, render_reply=False)

        gated_registry = _ConfirmationGateRegistry(
            registry, session_id=session_id, query=query
        )
        params = {"path": recent_path}
        outcome = gated_registry.execute_tool(contextual_action, params)
        pending_confirmation = _peek_pending_confirmation(session_id)
        if pending_confirmation is not None:
            result = _confirmation_result(pending_confirmation)
            return _present_result(
                result, query, memory, reply_renderer, render_reply=False
            )
        pending_file = _peek_pending_file_reference(session_id)
        if pending_file is not None:
            return _present_result(
                _file_clarification_result(pending_file), query, memory,
                reply_renderer, render_reply=False,
            )
        result = _tool_call_result(contextual_action, params, outcome)
        return _present_result(result, query, memory, reply_renderer)

    # Pronouns only inherit a target for an immediate follow-up. Any other
    # request ends that context so an old read can never silently retarget a
    # later destructive command.
    _clear_recent_file_context(session_id)

    try:
        memory_response = memory_command(query, memory)
    except (OSError, sqlite3.Error, ValueError) as exc:
        memory_response = f"Conversation memory is unavailable: {exc}"
    if memory_response is not None:
        # Do not record control commands: clearing must leave an empty store,
        # and displaying history must not recursively fill the history.
        return {"route": "memory_view", "response": memory_response}
    try:
        gated_registry = _ConfirmationGateRegistry(
            registry, session_id=session_id, query=query
        )
        if multi_step_mode:
            result = route_and_execute(query, gated_registry, multi_step_mode=True)
        else:
            # Keep the default call shape compatible with lightweight test and
            # embedding stubs that implement the original two-argument API.
            result = route_and_execute(query, gated_registry)
    except Exception as exc:
        result = {"route": "error", "response": f"Request failed: {exc}",
                  "status": "error", "success": False}
    pending = _peek_pending_confirmation(session_id)
    if pending is not None:
        result = _confirmation_result(pending)
        return _present_result(result, query, memory, reply_renderer, render_reply=False)
    pending_file = _peek_pending_file_reference(session_id)
    if pending_file is not None:
        return _present_result(_file_clarification_result(pending_file), query,
                               memory, reply_renderer, render_reply=False)
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
            memory = ConversationMemory(data_dir(ROOT_DIR) / "conversation.sqlite3")
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
        elif result["route"] == "multi_tool_call":
            print(result.get("display_response"))
        elif result["route"] == "memory_view":
            print(result.get("display_response") or result["response"])
        else:
            print(result.get("display_response") or result["response"])

        _print_benchmark("request", time.perf_counter() - started, rss_before, sample)


if __name__ == "__main__":
    main()
