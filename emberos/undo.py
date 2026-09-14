"""One session-local undo slot, owned by the executor, never by the model."""

from dataclasses import dataclass
from typing import Callable

from emberos.outcomes import ToolOutput


# Tool metadata, not classification of user text. Unknown/custom tools are
# barriers by default so undo cannot silently reach past an untracked action.
READ_ONLY_TOOLS = frozenset({
    "read_file", "list_dir", "get_clipboard", "get_active_window", "get_system_info",
    "find_files", "get_file_info", "disk_usage", "ram_status", "running_processes",
    "system_uptime", "cpu_info", "cpu_temperature", "summarize_file", "analyze_files",
    "grep_file", "grep_folder", "diff_files", "extract_patterns", "list_archive_contents",
    "find_large_files", "find_old_files", "find_duplicate_files", "get_image_info",
    "battery_status", "get_volume", "get_brightness", "get_open_windows", "list_tasks",
    "search_notes", "search_conversation_history", "batch_read_folder", "folder_explain",
})


class UndoUnavailable(RuntimeError):
    """No verified reversible change was captured."""


class UndoConflict(RuntimeError):
    """The target changed since the action; restoration has not started."""


@dataclass
class UndoEntry:
    tool: str
    restore: Callable[[], ToolOutput]
    target: str = ""


class UndoManager:
    def __init__(self):
        self.entry = None
        self.reason = "There is no action to undo in this session."

    def prepare(self, name, params):
        self.entry = None
        self.reason = f"The last action ({name}) cannot be undone."
        try:
            from use_cases.undo_actions import prepare_undo
            return prepare_undo(name, params)
        except Exception as exc:
            self.reason = f"The last action ({name}) has no verified undo snapshot: {str(exc)[:200]}"
            return None

    def complete(self, name, result, capture):
        if not result.success or result.status not in {"success", "returned"}:
            self.reason = f"The last action ({name}) failed or was incomplete; it cannot be safely undone."
            return
        if capture is None:
            return
        try:
            self.entry = capture(result)
        except Exception as exc:
            self.reason = f"The last action ({name}) cannot be undone: {str(exc)[:200]}"

    def undo(self, target: str = ""):
        if self.entry is None:
            return ToolOutput.failure(self.reason, status="unsupported")
        entry = self.entry
        if target and target != entry.target:
            return ToolOutput.failure(
                f"The last action was {entry.tool}; it was not a {target} change. No action was undone.",
                status="unsupported",
            )
        # Consume before calling the adapter: interruption or a partial restore
        # must never leave a repeatable destructive action or create redo state.
        self.entry = None
        self.reason = "There is no action to undo; the last undo has already been attempted."
        try:
            result = entry.restore()
            if not isinstance(result, ToolOutput):
                raise RuntimeError("Undo adapter did not report a verified outcome")
            return result
        except UndoConflict as exc:
            return ToolOutput.failure(f"Undo was not applied: {exc}")
        except Exception as exc:
            return ToolOutput.failure(f"Undo could not be verified: {str(exc)[:200]}", status="partial")
