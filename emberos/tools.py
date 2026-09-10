"""Tool registry and built-in tools for EmberOS-Windows."""

import concurrent.futures
import json
import inspect
import logging
import os
import subprocess
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from emberos.config import ROOT_DIR
from emberos.outcomes import ToolOutput

logger = logging.getLogger("emberos.tools")

# Tool logging to file
_tools_log_file = ROOT_DIR / "logs" / "tools.log"


def _log_tool_call(name: str, params: dict, result: dict) -> None:
    """Append tool call to the tools log file."""
    try:
        _tools_log_file.parent.mkdir(parents=True, exist_ok=True)
        import time
        entry = {
            "timestamp": time.time(),
            "tool": name,
            "params": params,
            "success": result.get("success", False),
            "status": result.get("status", "unknown"),
        }
        with open(_tools_log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


@dataclass
class ToolResult:
    success: bool
    result: Any = None
    error: str = ""
    status: str = ""
    message: str = ""
    data: Any = None

    def __post_init__(self):
        if not self.status:
            self.status = "returned" if self.success else "error"

    def to_dict(self) -> dict:
        return {"success": self.success, "result": self.result, "error": self.error,
                "status": self.status, "message": self.message, "data": self.data}


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict
    func: Callable


class ToolRegistry:
    """Central registry of callable tools."""

    def __init__(self, memory=None):
        self.memory = memory
        self._tools: dict[str, ToolDef] = {}
        self._register_builtins()

    def _search_conversation_history(self, query: str = ""):
        if self.memory is None:
            return ToolOutput.failure("Conversation memory is disabled or unavailable.", status="unsupported")
        return self.memory.recall(query)

    def register(self, name: str, description: str, parameters: dict, func: Callable) -> None:
        """Register a tool."""
        self._tools[name] = ToolDef(name=name, description=description,
                                     parameters=parameters, func=func)

    def get_tool(self, name: str) -> Optional[ToolDef]:
        return self._tools.get(name)

    def list_tools(self) -> list[dict]:
        """Return tool schemas for LLM consumption."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in self._tools.values()
        ]

    def execute_tool(self, name: str, params: dict) -> ToolResult:
        """Execute a registered tool by name."""
        tool = self._tools.get(name)
        if not tool:
            result = ToolResult(success=False, error=f"Unknown tool: {name}")
            _log_tool_call(name, params, result.to_dict())
            return result

        try:
            self.validate_arguments(name, params)
            output = tool.func(**params)
            if isinstance(output, ToolResult):
                result = output
            elif isinstance(output, ToolOutput):
                result = ToolResult(
                    success=output.success, result=str(output),
                    error="" if output.success else str(output), status=output.status,
                    message=output.message, data=output.data,
                )
            elif isinstance(output, dict) and output.get("error"):
                # Existing document/file tools use an explicit error field.
                result = ToolResult(success=False, result=output, error=str(output["error"]), data=output)
            else:
                # Returning text is not proof that an operation succeeded.
                # Legacy callers retain their value; the UI makes no OK claim.
                result = ToolResult(success=True, result=output, status="returned")
        except Exception as e:
            logger.exception("Tool '%s' failed", name)
            result = ToolResult(success=False, error=str(e))

        _log_tool_call(name, params, result.to_dict())
        return result

    def validate_arguments(self, name: str, params: dict) -> None:
        """Validate the declared contract before invoking any tool function."""
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"Unknown tool: {name}")
        if not isinstance(params, dict):
            raise ValueError("Tool arguments must be an object")
        signature = inspect.signature(tool.func)
        signature.bind(**params)
        types = {"string": str, "integer": int, "number": (int, float),
                 "boolean": bool, "array": list, "object": dict}
        for key, value in params.items():
            spec = tool.parameters.get(key, {})
            if value is None and signature.parameters[key].default is None:
                continue
            expected = spec.get("type")
            if expected in types and (not isinstance(value, types[expected]) or
                    expected in {"integer", "number"} and isinstance(value, bool)):
                raise ValueError(f"{key} must be {expected}")
            if "enum" in spec and value not in spec["enum"]:
                raise ValueError(f"{key} must be one of {spec['enum']}")
            if "minimum" in spec and value < spec["minimum"]:
                raise ValueError(f"{key} must be at least {spec['minimum']}")
            if "maximum" in spec and value > spec["maximum"]:
                raise ValueError(f"{key} must be at most {spec['maximum']}")

    def execute_parallel(self, calls: list[dict]) -> list[ToolResult]:
        """Execute multiple tool calls in parallel."""
        max_workers = min(8, os.cpu_count() or 4)
        results = [None] * len(calls)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {}
            for i, call in enumerate(calls):
                name = call.get("tool", "")
                params = call.get("params", {})
                future = executor.submit(self.execute_tool, name, params)
                future_map[future] = i

            for future in concurrent.futures.as_completed(future_map):
                idx = future_map[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    results[idx] = ToolResult(success=False, error=str(e))

        return results

    def _register_builtins(self) -> None:
        """Register all built-in tools."""

        self.register(
            name="run_shell",
            description="Run a shell command and return stdout/stderr",
            parameters={"cmd": {"type": "string", "description": "Shell command to execute"}},
            func=_tool_run_shell,
        )
        self.register(
            name="read_file",
            description="Read a file's content",
            parameters={"path": {"type": "string", "description": "File path to read"}},
            func=_tool_read_file,
        )
        self.register(
            name="write_file",
            description="Write content to a file",
            parameters={
                "path": {"type": "string", "description": "File path to write"},
                "content": {"type": "string", "description": "Content to write"},
            },
            func=_tool_write_file,
        )
        self.register(
            name="list_dir",
            description="List files and subfolders in a directory, including Downloads, Documents, or Desktop",
            parameters={"path": {"type": "string", "description": "Absolute directory path to list"}},
            func=_tool_list_dir,
        )
        self.register(
            name="get_clipboard",
            description="Read the current clipboard text",
            parameters={},
            func=_tool_get_clipboard,
        )
        self.register(
            name="set_clipboard",
            description="Write text to the clipboard",
            parameters={"text": {"type": "string", "description": "Text to copy"}},
            func=_tool_set_clipboard,
        )
        self.register(
            name="open_file",
            description="Open an existing filesystem file by its path. Do not use for application names such as File Explorer.",
            parameters={"path": {"type": "string", "description": "File path to open"}},
            func=_tool_open_file,
        )
        self.register(
            name="search_web",
            description="Open a URL in the default browser",
            parameters={"url": {"type": "string", "description": "URL to open"}},
            func=_tool_search_web,
        )
        self.register(
            name="get_active_window",
            description="Get the title of the currently active window",
            parameters={},
            func=_tool_get_active_window,
        )
        self.register(
            name="close_window",
            description="Close a window by its title",
            parameters={"title": {"type": "string", "description": "Window title to close"}},
            func=_tool_close_window,
        )
        self.register(
            name="get_system_info",
            description="Return hardware profile information",
            parameters={},
            func=_tool_get_system_info,
        )
        self.register(
            name="kill_process",
            description="Kill a process by name or PID",
            parameters={
                "target": {"type": "string", "description": "Process name or PID"},
            },
            func=_tool_kill_process,
        )

        # ── File operation tools ──────────────────────────────────
        self.register(
            name="find_files",
            description="Find files by name or type with optional date filter",
            parameters={
                "query": {"type": "string", "description": "File name substring to match"},
                "search_root": {"type": "string", "description": "Directory to search (default: home)"},
                "file_type": {"type": "string", "description": "Type filter: pdf, image, document, code, video, audio, archive, spreadsheet"},
                "modified_within_days": {"type": "integer", "description": "Only files modified within N days"},
            },
            func=_tool_find_files,
        )
        self.register(
            name="organize_folder",
            description="Organize a folder by file type into subfolders (PDFs, Images, Documents, etc.)",
            parameters={
                "folder": {"type": "string", "description": "Folder path to organize"},
                "preview_only": {"type": "boolean", "description": "If true, show preview without moving files"},
            },
            func=_tool_organize_folder,
        )
        self.register(
            name="move_file",
            description="Move a file or directory to a new location",
            parameters={
                "src": {"type": "string", "description": "Source path"},
                "dst": {"type": "string", "description": "Destination path"},
            },
            func=_tool_move_file,
        )
        self.register(
            name="copy_file",
            description="Copy a file or directory to a new location",
            parameters={
                "src": {"type": "string", "description": "Source path"},
                "dst": {"type": "string", "description": "Destination path"},
            },
            func=_tool_copy_file,
        )
        self.register(
            name="rename_file",
            description="Rename a file or directory",
            parameters={
                "path": {"type": "string", "description": "Path to rename"},
                "new_name": {"type": "string", "description": "New name (not full path)"},
            },
            func=_tool_rename_file,
        )
        self.register(
            name="delete_file",
            description="Delete a file or directory (snapshot backup created first)",
            parameters={
                "path": {"type": "string", "description": "Path to delete"},
            },
            func=_tool_delete_file,
        )
        self.register(
            name="get_file_info",
            description="Get file details: size, type, permissions, modification time",
            parameters={
                "path": {"type": "string", "description": "File or folder path"},
            },
            func=_tool_get_file_info,
        )
        self.register(
            name="create_directory",
            description="Create a new directory including parents",
            parameters={
                "path": {"type": "string", "description": "Directory path to create"},
            },
            func=_tool_create_directory,
        )

        # ── System query tools ────────────────────────────────────
        self.register(
            name="disk_usage",
            description="Get disk usage for all drives (size, used, free)",
            parameters={},
            func=_tool_disk_usage,
        )
        self.register(
            name="ram_status",
            description="Get current RAM usage and available memory",
            parameters={},
            func=_tool_ram_status,
        )
        self.register(
            name="running_processes",
            description="List running processes, optionally filtered by name",
            parameters={
                "filter_name": {"type": "string", "description": "Optional process name filter"},
            },
            func=_tool_running_processes,
        )
        self.register(
            name="system_uptime",
            description="Get how long the system has been running",
            parameters={},
            func=_tool_system_uptime,
        )
        self.register(
            name="cpu_info",
            description="Get CPU name, cores, frequency, and current usage",
            parameters={},
            func=_tool_cpu_info,
        )
        self.register(
            name="cpu_temperature",
            description="Get CPU temperature if available",
            parameters={},
            func=_tool_cpu_temperature,
        )

        # ── App launcher ──────────────────────────────────────────
        self.register(
            name="launch_app",
            description="Launch a desktop or Start Menu application by name (e.g. File Explorer, Spotify, calculator, browser, vscode, terminal). Use this, not open_file, when the user names an app.",
            parameters={
                "app_name": {"type": "string", "description": "Application name or alias"},
            },
            func=_tool_launch_app,
        )

        # ── Document analysis tools ───────────────────────────────
        self.register(
            name="summarize_file",
            description="Read and summarize any supported file (PDF, DOCX, XLSX, PPTX, TXT, CSV, etc.)",
            parameters={
                "path": {"type": "string", "description": "File path to summarize"},
            },
            func=_tool_summarize_file,
        )
        self.register(
            name="analyze_files",
            description="Analyze one or more attached files and answer a question about them",
            parameters={
                "file_paths": {"type": "array", "description": "List of file paths"},
                "user_message": {"type": "string", "description": "Question or task about the files"},
            },
            func=_tool_analyze_files,
        )
        self.register(
            name="grep_file",
            description="Search for a regex pattern inside a text file and return matching lines with context",
            parameters={
                "path": {"type": "string", "description": "File to search"},
                "pattern": {"type": "string", "description": "Regex or literal search pattern"},
                "context_lines": {"type": "integer", "description": "Lines of context around each match (default 2)"},
                "case_sensitive": {"type": "boolean", "description": "Case-sensitive search (default false)"},
            },
            func=_tool_grep_file,
        )
        self.register(
            name="grep_folder",
            description="Search for a text pattern or regex inside all readable files in a folder (PDFs, DOCX, TXT, etc.)",
            parameters={
                "folder": {"type": "string", "description": "Folder path to search in"},
                "pattern": {"type": "string", "description": "Regex or literal search pattern"},
                "extensions": {"type": "array", "description": "File extensions to include, e.g. ['pdf','txt'] (default: all readable)"},
                "case_sensitive": {"type": "boolean", "description": "Case-sensitive search (default false)"},
            },
            func=_tool_grep_folder,
        )
        self.register(
            name="diff_files",
            description="Show a unified diff between two text files",
            parameters={
                "path_a": {"type": "string", "description": "First file path"},
                "path_b": {"type": "string", "description": "Second file path"},
            },
            func=_tool_diff_files,
        )
        self.register(
            name="extract_patterns",
            description="Extract emails, URLs, phone numbers, dates, and IPs from a file",
            parameters={
                "path": {"type": "string", "description": "File to extract patterns from"},
                "pattern_types": {"type": "array", "description": "Types to extract: email, url, phone, date, ipv4 (default: all)"},
            },
            func=_tool_extract_patterns,
        )

        # ── Archive tools ─────────────────────────────────────────
        self.register(
            name="compress_to_zip",
            description="Compress files or folders into a zip archive",
            parameters={
                "sources": {"type": "array", "description": "List of file/folder paths to compress"},
                "dst": {"type": "string", "description": "Output zip path (optional)"},
            },
            func=_tool_compress_to_zip,
        )
        self.register(
            name="extract_archive",
            description="Extract a zip or tar archive",
            parameters={
                "src": {"type": "string", "description": "Archive file path"},
                "dst": {"type": "string", "description": "Destination folder (optional)"},
            },
            func=_tool_extract_archive,
        )
        self.register(
            name="list_archive_contents",
            description="List files inside a zip or tar archive without extracting",
            parameters={
                "src": {"type": "string", "description": "Archive file path"},
            },
            func=_tool_list_archive_contents,
        )

        # ── File discovery tools ──────────────────────────────────
        self.register(
            name="find_large_files",
            description="Find files larger than a size threshold",
            parameters={
                "root": {"type": "string", "description": "Directory to search (default: home)"},
                "min_mb": {"type": "number", "description": "Minimum size in MB (default: 100)"},
                "limit": {"type": "integer", "description": "Max results (default: 20)"},
            },
            func=_tool_find_large_files,
        )
        self.register(
            name="find_old_files",
            description="Find files not modified for a long time",
            parameters={
                "root": {"type": "string", "description": "Directory to search (default: home)"},
                "older_than_days": {"type": "integer", "description": "Files not modified in N days (default: 365)"},
                "limit": {"type": "integer", "description": "Max results (default: 20)"},
            },
            func=_tool_find_old_files,
        )
        self.register(
            name="find_duplicate_files",
            description="Find duplicate files (same content) using MD5 hashing",
            parameters={
                "root": {"type": "string", "description": "Directory to search (default: home)"},
                "limit": {"type": "integer", "description": "Max duplicate groups to return (default: 50)"},
            },
            func=_tool_find_duplicate_files,
        )

        # ── Media / image tools ───────────────────────────────────
        self.register(
            name="take_screenshot",
            description="Capture a full-screen screenshot and save it",
            parameters={
                "save_path": {"type": "string", "description": "Output file path (optional, defaults to Pictures/EmberOS Screenshots)"},
            },
            func=_tool_take_screenshot,
        )
        self.register(
            name="resize_image",
            description="Resize an image to specified dimensions",
            parameters={
                "src": {"type": "string", "description": "Source image path"},
                "width": {"type": "integer", "description": "Target width in pixels"},
                "height": {"type": "integer", "description": "Target height in pixels"},
                "dst": {"type": "string", "description": "Output path (optional)"},
            },
            func=_tool_resize_image,
        )
        self.register(
            name="convert_image",
            description="Convert an image to a different format (PNG, JPEG, BMP, etc.)",
            parameters={
                "src": {"type": "string", "description": "Source image path"},
                "target_format": {"type": "string", "description": "Target format: png, jpg, bmp, webp, etc."},
                "dst": {"type": "string", "description": "Output path (optional)"},
            },
            func=_tool_convert_image,
        )
        self.register(
            name="rotate_image",
            description="Rotate an image by a given number of degrees",
            parameters={
                "src": {"type": "string", "description": "Source image path"},
                "degrees": {"type": "number", "description": "Rotation degrees (counter-clockwise)"},
                "dst": {"type": "string", "description": "Output path (optional)"},
            },
            func=_tool_rotate_image,
        )
        self.register(
            name="get_image_info",
            description="Get dimensions, mode, format, and size of an image",
            parameters={
                "src": {"type": "string", "description": "Image file path"},
            },
            func=_tool_get_image_info,
        )
        self.register(
            name="extract_audio",
            description="Extract audio track from a video file (requires FFmpeg)",
            parameters={
                "video_src": {"type": "string", "description": "Video file path"},
                "dst": {"type": "string", "description": "Output audio path (optional, defaults to MP3)"},
            },
            func=_tool_extract_audio,
        )
        self.register(
            name="extract_video_clip",
            description="Extract a time-range clip from a video file (requires FFmpeg)",
            parameters={
                "src": {"type": "string", "description": "Video file path"},
                "start": {"type": "string", "description": "Start time (e.g. 00:01:30 or 90)"},
                "duration": {"type": "string", "description": "Clip duration (e.g. 00:00:30 or 30)"},
                "dst": {"type": "string", "description": "Output path (optional)"},
            },
            func=_tool_extract_video_clip,
        )

        # ── System control tools ──────────────────────────────────
        self.register(
            name="battery_status",
            description="Check remaining battery charge percentage and charging state; not screen brightness",
            parameters={},
            func=_tool_battery_status,
        )
        self.register(
            name="lock_screen",
            description="Lock the Windows screen",
            parameters={},
            func=_tool_lock_screen,
        )
        self.register(
            name="sleep_system",
            description="Put the computer to sleep",
            parameters={},
            func=_tool_sleep_system,
        )
        self.register(
            name="shutdown_system",
            description="Shut down the computer",
            parameters={
                "delay_seconds": {"type": "integer", "description": "Delay before shutdown in seconds (default: 0)"},
            },
            func=_tool_shutdown_system,
        )
        self.register(
            name="restart_system",
            description="Restart the computer",
            parameters={
                "delay_seconds": {"type": "integer", "description": "Delay before restart in seconds (default: 0)"},
            },
            func=_tool_restart_system,
        )
        self.register(
            name="cancel_shutdown",
            description="Cancel a pending scheduled shutdown or restart",
            parameters={},
            func=_tool_cancel_shutdown,
        )
        self.register(
            name="volume_up",
            description="Increase audio volume from its current level by a number of steps",
            parameters={
                "steps": {"type": "integer", "description": "Number of volume steps to increase (default: 2)"},
            },
            func=_tool_volume_up,
        )
        self.register(
            name="volume_down",
            description="Decrease audio volume from its current level by a number of steps",
            parameters={
                "steps": {"type": "integer", "description": "Number of volume steps to decrease (default: 2)"},
            },
            func=_tool_volume_down,
        )
        self.register(
            name="mute_volume",
            description="Toggle mute on system volume",
            parameters={},
            func=_tool_mute_volume,
        )
        self.register(
            name="get_volume",
            description="Get the current system volume level",
            parameters={},
            func=_tool_get_volume,
        )
        self.register(
            name="set_volume",
            description="Set the system audio speaker volume to an absolute percentage from 0 to 100",
            parameters={
                "level": {"type": "integer", "minimum": 0, "maximum": 100,
                          "description": "Target audio volume percentage, not a relative number of steps"},
            },
            func=_tool_set_volume,
        )
        self.register(
            name="get_brightness",
            description="Get the current screen brightness level",
            parameters={},
            func=_tool_get_brightness,
        )
        self.register(
            name="set_brightness",
            description="Set the screen brightness (0-100)",
            parameters={
                "level": {"type": "integer", "minimum": 0, "maximum": 100, "description": "Brightness level 0-100"},
            },
            func=_tool_set_brightness,
        )
        self.register(
            name="toggle_dark_mode",
            description="Toggle Windows dark/light mode",
            parameters={},
            func=_tool_toggle_dark_mode,
        )
        self.register(
            name="set_dark_mode",
            description="Enable or disable Windows dark mode explicitly",
            parameters={
                "enable": {"type": "boolean", "description": "True for dark mode, False for light mode"},
            },
            func=_tool_set_dark_mode,
        )

        # ── Window management tools ───────────────────────────────
        self.register(
            name="get_open_windows",
            description="List all currently open windows",
            parameters={},
            func=_tool_get_open_windows,
        )
        self.register(
            name="minimize_all_windows",
            description="Minimize all windows and show the desktop",
            parameters={},
            func=_tool_minimize_all_windows,
        )
        self.register(
            name="focus_window",
            description="Bring a window to the foreground by title",
            parameters={
                "title_fragment": {"type": "string", "description": "Part of the window title to match"},
            },
            func=_tool_focus_window,
        )

        # ── Task management tools ─────────────────────────────────
        self.register(
            name="add_task",
            description="Add a new to-do task",
            parameters={
                "title": {"type": "string", "description": "Task description"},
                "due_date": {"type": "string", "description": "Optional due date (ISO format or natural text)"},
                "priority": {"type": "string", "enum": ["low", "normal", "high"], "description": "Priority: low, normal, high (default: normal)"},
            },
            func=_tool_add_task,
        )
        self.register(
            name="list_tasks",
            description="List pending or all tasks",
            parameters={
                "show_all": {"type": "boolean", "description": "Show completed tasks too (default: false)"},
            },
            func=_tool_list_tasks,
        )
        self.register(
            name="complete_task",
            description="Mark a task as completed by its ID",
            parameters={
                "task_id": {"type": "integer", "description": "Task ID to mark complete"},
            },
            func=_tool_complete_task,
        )
        self.register(
            name="remove_task",
            description="Delete a task by its ID",
            parameters={
                "task_id": {"type": "integer", "description": "Task ID to delete"},
            },
            func=_tool_remove_task,
        )
        self.register(
            name="clear_completed_tasks",
            description="Remove all completed tasks from the list",
            parameters={},
            func=_tool_clear_completed_tasks,
        )

        # ── Notes tools ──────────────────────────────────────────
        self.register(
            name="create_note",
            description="Save a new note as a TXT file and open it in Notepad; also keep a searchable copy, optionally tagged",
            parameters={
                "title": {"type": "string", "description": "Short title for the note"},
                "content": {"type": "string", "description": "The note's content/body"},
                "tags": {"type": "array", "description": "Optional list of tags (optional)"},
            },
            func=_tool_create_note,
        )
        self.register(
            name="search_notes",
            description="Search previously saved notes by title, content, or tag",
            parameters={
                "query": {"type": "string", "description": "Search text to match against notes"},
            },
            func=_tool_search_notes,
        )
        self.register(
            name="search_conversation_history",
            description="Retrieve past conversations with Ember by topic, including previous requests and reported outcomes",
            parameters={
                "query": {"type": "string", "description": "Topic words from the request; empty for recent conversations"},
            },
            func=self._search_conversation_history,
        )

        # ── Multi-document / synthesis tools ──────────────────────
        self.register(
            name="create_spreadsheet",
            description="Create an Excel XLSX workbook from rows of data and open it in the default spreadsheet application",
            parameters={
                "path": {"type": "string", "description": "New output file path ending in .xlsx"},
                "rows": {"type": "array", "description": "List of rows, e.g. [['Item', 'Quantity'], ['Milk', 2]]", "items": {"type": "array"}},
                "sheet_name": {"type": "string", "description": "Worksheet name (optional; default Sheet1)"},
                "open_after": {"type": "boolean", "description": "Open the saved file (optional; default true)"},
            },
            func=_tool_create_spreadsheet,
        )
        self.register(
            name="create_google_doc",
            description="Create a Google Docs document in the connected Google account, insert text, and open its link in the browser",
            parameters={
                "title": {"type": "string", "description": "Title of the new Google document"},
                "content": {"type": "string", "description": "Text to put in the Google document"},
                "open_after": {"type": "boolean", "description": "Open the document in the browser (optional; default true)"},
            },
            func=_tool_create_google_doc,
        )
        self.register(
            name="batch_read_folder",
            description=(
                "Read all readable documents in a folder "
                "(PDF, DOCX, XLSX, PPTX, TXT, CSV, Markdown, etc.) "
                "and return their combined text content"
            ),
            parameters={
                "folder": {"type": "string", "description": "Folder path to read documents from"},
                "extensions": {
                    "type": "array",
                    "description": "Optional list of extensions to include, e.g. ['pdf', 'txt']",
                },
                "max_chars_per_file": {
                    "type": "integer",
                    "description": "Max characters to read per file (default: 4000)",
                },
            },
            func=_tool_batch_read_folder,
        )
        self.register(
            name="folder_explain",
            description=(
                "Return a structured description of what a folder contains: "
                "file counts by type, total size, and the five largest files"
            ),
            parameters={
                "folder": {"type": "string", "description": "Folder path to examine"},
            },
            func=_tool_folder_explain,
        )
        self.register(
            name="write_document",
            description=(
                "Write text content to a file. "
                "Supports TXT, Markdown (.md), PDF (requires fpdf2), DOCX (requires python-docx), and XLSX (CSV content; requires openpyxl). "
                "Falls back to plain text when format libraries are absent."
            ),
            parameters={
                "path": {
                    "type": "string",
                    "description": "Output file path including filename and extension",
                },
                "content": {"type": "string", "description": "Text content to write"},
                "fmt": {
                    "type": "string",
                    "description": "Format override: txt, md, pdf, docx, xlsx (optional; inferred from extension)",
                },
            },
            func=_tool_write_document,
        )


# ── Built-in tool implementations ──────────────────────────────────

def _tool_run_shell(cmd: str) -> str:
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=60,
        cwd=str(ROOT_DIR),
    )
    output = result.stdout
    if result.stderr:
        output += "\n[STDERR]\n" + result.stderr
    if result.returncode != 0:
        return ToolOutput.failure(output.strip() or f"Command exited with code {result.returncode}.",
                                  data={"returncode": result.returncode})
    return ToolOutput(output.strip(), message="The command finished.", data={"returncode": 0})


def _tool_read_file(path: str) -> str:
    from use_cases.file_analysis import read_attached_file
    p = Path(path)
    if not p.is_absolute():
        p = ROOT_DIR / p
    return read_attached_file(str(p))


def _tool_write_file(path: str, content: str) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT_DIR / p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Written {len(content)} bytes to {p}"


def _tool_list_dir(path: str) -> list:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT_DIR / p
    return sorted(
        (e.name + "/" if e.is_dir() else e.name) for e in p.iterdir()
    )


def _tool_get_clipboard() -> str:
    import pyperclip
    return pyperclip.paste() or ""


def _tool_set_clipboard(text: str) -> str:
    import pyperclip
    pyperclip.copy(text)
    return "Clipboard updated"


def _tool_open_file(path: str) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT_DIR / p
    os.startfile(str(p))
    return f"Opened {p}"


def _tool_search_web(url: str) -> str:
    if not webbrowser.open(url):
        return ToolOutput.failure(f"I couldn't open {url} in a browser.")
    return ToolOutput(f"Sent {url} to your browser.")


def _tool_get_active_window() -> str:
    import pygetwindow as gw
    win = gw.getActiveWindow()
    return win.title if win else "(no active window)"


def _tool_close_window(title: str) -> str:
    import pygetwindow as gw
    windows = gw.getWindowsWithTitle(title)
    if not windows:
        return ToolOutput.failure(f"No window found with title: {title}")
    windows[0].close()
    return f"Closed window: {title}"


def _tool_get_system_info() -> dict:
    from emberos.gpu_detect import detect_hardware
    profile = detect_hardware()
    return {
        "cpu_arch": profile.cpu_arch,
        "cpu_cores": profile.cpu_cores,
        "cpu_threads": profile.cpu_threads,
        "ram_gb": profile.ram_gb,
        "gpu_available": profile.gpu_available,
        "gpu_name": profile.gpu_name,
        "gpu_vram_mb": profile.gpu_vram_mb,
        "gpu_mode": profile.gpu_mode,
        "cuda_version": profile.cuda_version,
    }


def _tool_kill_process(target: str) -> str:
    import psutil
    killed = []
    # Try as PID first
    try:
        pid = int(target)
        proc = psutil.Process(pid)
        proc.kill()
        return f"Killed process PID {pid}"
    except (ValueError, psutil.NoSuchProcess):
        pass

    # Try by name
    for proc in psutil.process_iter(["name", "pid"]):
        try:
            if proc.info["name"] and target.lower() in proc.info["name"].lower():
                proc.kill()
                killed.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if killed:
        return f"Killed {len(killed)} process(es) matching '{target}': PIDs {killed}"
    return ToolOutput.failure(f"No process found matching '{target}'")


# ── File operation tool implementations ────────────────────────────

def _tool_find_files(query: str = "", search_root: str = None,
                     file_type: str = None, modified_within_days: int = None) -> list:
    from use_cases.file_ops import find_files
    return find_files(query or "", search_root, file_type, modified_within_days)


def _tool_organize_folder(folder: str, preview_only: bool = True) -> dict:
    from use_cases.file_ops import organize_folder_by_type
    return organize_folder_by_type(folder, preview_only=bool(preview_only))


def _tool_move_file(src: str, dst: str) -> str:
    from use_cases.file_ops import move_file
    return move_file(src, dst)


def _tool_copy_file(src: str, dst: str) -> str:
    from use_cases.file_ops import copy_file
    return copy_file(src, dst)


def _tool_rename_file(path: str, new_name: str) -> str:
    from use_cases.file_ops import rename_file
    return rename_file(path, new_name)


def _tool_delete_file(path: str) -> str:
    from use_cases.file_ops import delete_file
    return delete_file(path)


def _tool_get_file_info(path: str) -> dict:
    from use_cases.file_ops import get_file_info
    return get_file_info(path)


def _tool_create_directory(path: str) -> str:
    from use_cases.file_ops import create_directory
    return create_directory(path)


# ── System query tool implementations ──────────────────────────────

def _tool_disk_usage() -> str:
    from use_cases.system_queries import get_disk_usage
    return get_disk_usage()


def _tool_ram_status() -> str:
    from use_cases.system_queries import get_ram_status
    return get_ram_status()


def _tool_running_processes(filter_name: str = None) -> str:
    from use_cases.system_queries import get_running_processes
    return get_running_processes(filter_name)


def _tool_system_uptime() -> str:
    from use_cases.system_queries import get_system_uptime
    return get_system_uptime()


def _tool_cpu_info() -> str:
    from use_cases.system_queries import get_cpu_info
    return get_cpu_info()


def _tool_cpu_temperature() -> str:
    from use_cases.system_queries import check_cpu_temperature
    return check_cpu_temperature()


# ── App launcher tool implementation ───────────────────────────────

def _tool_launch_app(app_name: str) -> str:
    from use_cases.app_launcher import launch_app
    return launch_app(app_name)


# ── Document analysis tool implementations ─────────────────────────

def _tool_summarize_file(path: str) -> str:
    from use_cases.file_analysis import summarize_file
    from smollm_fallback import get_local_text_client

    try:
        # No model loads on entry. Validation precedes the first generation.
        # All document passes share one worker, reaped even on failure/cancel.
        with get_local_text_client() as llm_client:
            result = summarize_file(path, llm_client=llm_client)
    except Exception as exc:
        logger.warning("Local summary worker unavailable: %s", exc)
        result = summarize_file(path, llm_client=None)
    if result is None:
        return ToolOutput.failure(f"File not found: {path}")
    return result


def _tool_analyze_files(file_paths: list, user_message: str) -> str:
    from use_cases.file_analysis import analyze_attached_files
    return analyze_attached_files(file_paths, user_message)


def _tool_grep_file(path: str, pattern: str, context_lines: int = 2,
                    case_sensitive: bool = False) -> str:
    from use_cases.file_analysis import grep_file
    return grep_file(path, pattern, context_lines, case_sensitive)


def _tool_diff_files(path_a: str, path_b: str) -> str:
    from use_cases.file_analysis import diff_files
    return diff_files(path_a, path_b)


def _tool_extract_patterns(path: str, pattern_types: list = None) -> dict:
    from use_cases.file_analysis import extract_patterns
    return extract_patterns(path, pattern_types)


# ── Archive tool implementations ────────────────────────────────────

def _tool_compress_to_zip(sources: list, dst: str = None) -> str:
    from use_cases.file_ops import compress_to_zip
    return compress_to_zip(sources, dst)


def _tool_extract_archive(src: str, dst: str = None) -> str:
    from use_cases.file_ops import extract_archive
    return extract_archive(src, dst)


def _tool_list_archive_contents(src: str) -> str:
    from use_cases.file_ops import list_archive_contents
    return list_archive_contents(src)


# ── File discovery tool implementations ─────────────────────────────

def _tool_find_large_files(root: str = None, min_mb: float = 100,
                            limit: int = 20) -> list:
    from use_cases.file_ops import find_large_files
    return find_large_files(root, min_mb, limit)


def _tool_find_old_files(root: str = None, older_than_days: int = 365,
                          limit: int = 20) -> list:
    from use_cases.file_ops import find_old_files
    return find_old_files(root, older_than_days, limit)


def _tool_find_duplicate_files(root: str = None, limit: int = 50) -> dict:
    from use_cases.file_ops import find_duplicate_files
    return find_duplicate_files(root, limit)


# ── Media tool implementations ──────────────────────────────────────

def _tool_take_screenshot(save_path: str = None) -> str:
    from use_cases.media_ops import take_screenshot
    return take_screenshot(save_path)


def _tool_resize_image(src: str, width: int, height: int, dst: str = None) -> str:
    from use_cases.media_ops import resize_image
    return resize_image(src, width, height, dst)


def _tool_convert_image(src: str, target_format: str, dst: str = None) -> str:
    from use_cases.media_ops import convert_image
    return convert_image(src, target_format, dst)


def _tool_rotate_image(src: str, degrees: float, dst: str = None) -> str:
    from use_cases.media_ops import rotate_image
    return rotate_image(src, degrees, dst)


def _tool_get_image_info(src: str) -> str:
    from use_cases.media_ops import get_image_info
    return get_image_info(src)


def _tool_extract_audio(video_src: str, dst: str = None) -> str:
    from use_cases.media_ops import extract_audio
    return extract_audio(video_src, dst)


def _tool_extract_video_clip(src: str, start: str, duration: str,
                              dst: str = None) -> str:
    from use_cases.media_ops import extract_video_clip
    return extract_video_clip(src, start, duration, dst)


# ── System control tool implementations ─────────────────────────────

def _tool_battery_status() -> str:
    from use_cases.system_queries import get_battery_status
    return get_battery_status()


def _tool_lock_screen() -> str:
    from use_cases.system_queries import lock_screen
    return lock_screen()


def _tool_sleep_system() -> str:
    from use_cases.system_queries import sleep_system
    return sleep_system()


def _tool_shutdown_system(delay_seconds: int = 0) -> str:
    from use_cases.system_queries import shutdown_system
    return shutdown_system(delay_seconds)


def _tool_restart_system(delay_seconds: int = 0) -> str:
    from use_cases.system_queries import restart_system
    return restart_system(delay_seconds)


def _tool_cancel_shutdown() -> str:
    from use_cases.system_queries import cancel_shutdown
    return cancel_shutdown()


def _tool_volume_up(steps: int = 2) -> str:
    from use_cases.system_queries import volume_up
    return volume_up(steps)


def _tool_volume_down(steps: int = 2) -> str:
    from use_cases.system_queries import volume_down
    return volume_down(steps)


def _tool_mute_volume() -> str:
    from use_cases.system_queries import mute_volume
    return mute_volume()


def _tool_get_volume() -> str:
    from use_cases.system_queries import get_volume
    return get_volume()


def _tool_set_volume(level: int) -> str:
    from use_cases.system_queries import set_volume
    return set_volume(level)


def _tool_get_brightness() -> str:
    from use_cases.system_queries import get_brightness
    return get_brightness()


def _tool_set_brightness(level: int) -> str:
    from use_cases.system_queries import set_brightness
    return set_brightness(level)


def _tool_toggle_dark_mode() -> str:
    from use_cases.system_queries import toggle_dark_mode
    return toggle_dark_mode()


def _tool_set_dark_mode(enable: bool) -> str:
    from use_cases.system_queries import set_dark_mode
    return set_dark_mode(enable)


# ── Window management tool implementations ───────────────────────────

def _tool_get_open_windows() -> str:
    from use_cases.system_queries import get_open_windows
    return get_open_windows()


def _tool_minimize_all_windows() -> str:
    from use_cases.system_queries import minimize_all_windows
    return minimize_all_windows()


def _tool_focus_window(title_fragment: str) -> str:
    from use_cases.system_queries import focus_window
    return focus_window(title_fragment)


# ── Task management tool implementations ─────────────────────────────

_task_manager = None


def _get_task_manager():
    global _task_manager
    if _task_manager is None:
        from use_cases.tasks import TaskManager
        _task_manager = TaskManager()
    return _task_manager


def _tool_add_task(title: str, due_date: str = None, priority: str = "normal") -> str:
    task = _get_task_manager().add(title, due_date, priority)
    return ToolOutput(f"Done! Added task #{task['id']}: {task['title']} (priority: {task['priority']}).", data=task)


def _tool_list_tasks(show_all: bool = False) -> str:
    mgr = _get_task_manager()
    tasks = mgr.list_all() if show_all else mgr.list_pending()
    if not tasks:
        return "No tasks found." if show_all else "No pending tasks."
    lines = []
    for t in tasks:
        done = "[x]" if t["completed"] else "[ ]"
        pri  = f"[{t['priority']}]" if t["priority"] != "normal" else ""
        due  = f" — due: {t['due_date']}" if t["due_date"] else ""
        lines.append(f"  #{t['id']} {done} {pri} {t['title']}{due}")
    label = "All tasks" if show_all else "Pending tasks"
    return f"{label} ({len(tasks)}):\n" + "\n".join(lines)


def _tool_complete_task(task_id: int) -> str:
    task = _get_task_manager().complete(int(task_id))
    if task is None:
        return ToolOutput.failure(f"Task #{task_id} not found or already completed.")
    return ToolOutput(f"Done! Completed task #{task_id}: {task['title']}.", data=task)


def _tool_remove_task(task_id: int) -> str:
    removed = _get_task_manager().remove(int(task_id))
    return ToolOutput(f"Done! Deleted task #{task_id}.") if removed else ToolOutput.failure(f"Task #{task_id} not found.")


def _tool_clear_completed_tasks() -> str:
    count = _get_task_manager().clear_completed()
    return f"Cleared {count} completed task(s)."


# ── Notes tool implementations ────────────────────────────────────

_notes_manager = None


def _get_notes_manager():
    global _notes_manager
    if _notes_manager is None:
        from use_cases.notes import NotesManager
        _notes_manager = NotesManager()
    return _notes_manager


def _tool_create_note(title: str, content: str, tags: list = None) -> str:
    from use_cases.notes import open_in_notepad

    manager = _get_notes_manager()
    note = manager.add(title, content, tags)
    tags_str = ", ".join(note["tags"]) if note["tags"] else "none"
    summary = f"Note #{note['id']} saved: {note['title']} (tags: {tags_str})"
    try:
        path = manager.export_text(note)
    except OSError as exc:
        raise RuntimeError(
            f"{summary}\nThe database copy is saved, but TXT export failed: {exc}"
        ) from exc
    summary += f"\nTXT file: {path}"
    try:
        open_in_notepad(path)
    except OSError as exc:
        return ToolOutput.failure(f"{summary}\nCould not open Notepad: {exc}. Open the saved TXT file manually.",
                                  status="partial", data={"path": str(path), "saved": True, "opened": False})
    return ToolOutput(f"{summary}\nSent to Notepad.", data={"path": str(path), "saved": True, "opened": True})


def _tool_search_notes(query: str) -> str:
    results = _get_notes_manager().search(query)
    if not results:
        return f"No notes found matching '{query}'."
    lines = [f"  #{n['id']} {n['title']}: {n['content']}" for n in results]
    return f"Found {len(results)} note(s):\n" + "\n".join(lines)


# ── Multi-document / synthesis tool implementations ──────────────────

def _tool_create_spreadsheet(path: str, rows: list, sheet_name: str = "Sheet1",
                             open_after: bool = True) -> str:
    from use_cases.spreadsheets import create_spreadsheet
    return create_spreadsheet(path, rows, sheet_name, open_after)


def _tool_create_google_doc(title: str, content: str, open_after: bool = True) -> str:
    from use_cases.google_docs import create_google_doc
    return create_google_doc(title, content, open_after)


def _tool_batch_read_folder(folder: str, extensions: list = None,
                             max_chars_per_file: int = 4000) -> dict:
    from use_cases.file_analysis import batch_read_folder
    return batch_read_folder(folder, extensions, max_chars_per_file)


def _tool_folder_explain(folder: str) -> dict:
    from use_cases.file_analysis import folder_explain
    return folder_explain(folder)


def _tool_write_document(path: str, content: str, fmt: str = None) -> str:
    from use_cases.doc_gen import write_document
    return write_document(path, content, fmt)


def _tool_grep_folder(folder: str, pattern: str, extensions: list = None,
                      case_sensitive: bool = False) -> dict:
    from use_cases.file_analysis import grep_folder
    return grep_folder(folder, pattern, extensions=extensions, case_sensitive=case_sensitive)
