"""
needle_router.py

Modul inti: skema tool buat Needle (di-mirror dari tools.py
ToolRegistry._register_builtins) + route_and_execute() buat mutusin
tool call mana yang match dan eksekusi lewat ToolRegistry asli EmberOS.

Nama & parameter tiap tool di sini HARUS persis sama kayak tools.py --
bukan penamaan lama router.py (mis. "screenshot" vs "take_screenshot").

Fungsi-fungsi @needle.tool di bawah badannya kosong (pass) -- Needle cuma
butuh signature + docstring buat bangun skema matching, gak menjalankan
fungsi ini. Eksekusi beneran lewat ToolRegistry.execute_tool() di
route_and_execute().

Default-value params harus sama dengan implementasi di ToolRegistry.

Dipakai dari luar: import route_and_execute, panggil dengan (query, registry).
Buat contoh pemakaian/test, lihat experiments/test_router.py.
"""

import json
import os
import re
from pathlib import Path

import needle

# ── Core file/system tools ──────────────────────────────────────────
@needle.tool
def run_shell(cmd: str):
    "Run a shell command and return stdout/stderr"
    pass

@needle.tool
def read_file(path: str):
    "Read a file's content"
    pass

@needle.tool
def write_file(path: str, content: str):
    "Write content to a file"
    pass

@needle.tool
def list_dir(path: str):
    """List the files and subfolders in a directory.

    Use for requests such as "list files in Downloads" or "show the
    contents of my Documents folder". The path must be the folder to list.
    """
    pass

@needle.tool
def get_clipboard():
    "Read the current clipboard text"
    pass

@needle.tool
def set_clipboard(text: str):
    "Write text to the clipboard"
    pass

@needle.tool
def open_file(path: str):
    "Open an existing filesystem file by path. Never use this for an app name such as File Explorer."
    pass

@needle.tool
def search_web(url: str):
    """Open a specific URL the user explicitly stated in their message.

    Only use when the user's message contains an actual web address
    (e.g. "open github.com", "go to youtube.com/watch?v=..."). Never invent
    or guess a URL for an app or product name the user did not give a URL
    for -- use launch_app for that instead.
    """
    pass

@needle.tool
def get_active_window():
    "Get the title of the currently active window"
    pass

@needle.tool
def close_window(title: str):
    "Close a window by its title"
    pass

@needle.tool
def get_system_info():
    "Return hardware profile information"
    pass

@needle.tool
def kill_process(target: str):
    "Kill a process by name or PID"
    pass

# ── File operation tools ─────────────────────────────────────────────
@needle.tool
def find_files(query: str = "", search_root: str = None, file_type: str = None,
               modified_within_days: int = None):
    "Find files by name or type with optional date filter"
    pass

@needle.tool
def organize_folder(folder: str, preview_only: bool = True):
    "Organize a folder by file type into subfolders (PDFs, Images, Documents, etc.)"
    pass

@needle.tool
def move_file(src: str, dst: str):
    "Move a file or directory to a new location"
    pass

@needle.tool
def copy_file(src: str, dst: str):
    "Copy a file or directory to a new location"
    pass

@needle.tool
def rename_file(path: str, new_name: str):
    "Rename a file or directory"
    pass

@needle.tool
def delete_file(path: str):
    "Delete a file or directory (snapshot backup created first)"
    pass

@needle.tool
def get_file_info(path: str):
    "Get file details: size, type, permissions, modification time"
    pass

@needle.tool
def create_directory(path: str):
    "Create a new directory including parents"
    pass

# ── System query tools ───────────────────────────────────────────────
@needle.tool
def disk_usage():
    "Get disk usage for all drives (size, used, free)"
    pass

@needle.tool
def ram_status():
    "Get current RAM usage and available memory"
    pass

@needle.tool
def running_processes(filter_name: str = None):
    "List running processes, optionally filtered by name"
    pass

@needle.tool
def system_uptime():
    "Get how long the system has been running"
    pass

@needle.tool
def cpu_info():
    "Get CPU name, cores, frequency, and current usage"
    pass

@needle.tool
def cpu_temperature():
    "Get CPU temperature if available"
    pass

# ── App launcher ──────────────────────────────────────────────────────
@needle.tool
def launch_app(app_name: str):
    """Launch or open a desktop or Start Menu application by name.

    Use this whenever the request is "open <app name>" or "launch <app name>"
    and app_name is a program (File Explorer, calculator, browser, vscode, terminal,
    notepad, spotify, netflix, sticky notes, etc.), NOT a specific file path and NOT a
    web URL. If app_name is not an installed program, do not invent a URL for it.
    """
    pass

# ── Document analysis tools ────────────────────────────────────────────
@needle.tool
def summarize_file(path: str):
    "Read and summarize any supported file (PDF, DOCX, XLSX, PPTX, TXT, CSV, etc.)"
    pass

@needle.tool
def analyze_files(file_paths: list, user_message: str):
    "Analyze one or more attached files and answer a question about them"
    pass

@needle.tool
def grep_file(path: str, pattern: str, context_lines: int = 2, case_sensitive: bool = False):
    "Search for a regex pattern inside a text file and return matching lines with context"
    pass

@needle.tool
def grep_folder(folder: str, pattern: str, extensions: list = None, case_sensitive: bool = False):
    "Search for a text pattern or regex inside all readable files in a folder (PDFs, DOCX, TXT, etc.)"
    pass

@needle.tool
def diff_files(path_a: str, path_b: str):
    "Show a unified diff between two text files"
    pass

@needle.tool
def extract_patterns(path: str, pattern_types: list = None):
    "Extract emails, URLs, phone numbers, dates, and IPs from a file"
    pass

# ── Archive tools ────────────────────────────────────────────────────
@needle.tool
def compress_to_zip(sources: list, dst: str = None):
    "Compress files or folders into a zip archive"
    pass

@needle.tool
def extract_archive(src: str, dst: str = None):
    "Extract a zip or tar archive"
    pass

@needle.tool
def list_archive_contents(src: str):
    "List files inside a zip or tar archive without extracting"
    pass

# ── File discovery tools ─────────────────────────────────────────────
@needle.tool
def find_large_files(root: str = None, min_mb: float = 100, limit: int = 20):
    "Find files larger than a size threshold"
    pass

@needle.tool
def find_old_files(root: str = None, older_than_days: int = 365, limit: int = 20):
    "Find files not modified for a long time"
    pass

@needle.tool
def find_duplicate_files(root: str = None, limit: int = 50):
    "Find duplicate files (same content) using MD5 hashing"
    pass

# ── Media / image tools ──────────────────────────────────────────────
@needle.tool
def take_screenshot(save_path: str = None):
    "Capture a full-screen screenshot and save it"
    pass

@needle.tool
def resize_image(src: str, width: int, height: int, dst: str = None):
    "Resize an image to specified dimensions"
    pass

@needle.tool
def convert_image(src: str, target_format: str, dst: str = None):
    "Convert an image to a different format (PNG, JPEG, BMP, etc.)"
    pass

@needle.tool
def rotate_image(src: str, degrees: float, dst: str = None):
    "Rotate an image by a given number of degrees"
    pass

@needle.tool
def get_image_info(src: str):
    "Get dimensions, mode, format, and size of an image"
    pass

@needle.tool
def extract_audio(video_src: str, dst: str = None):
    "Extract audio track from a video file (requires FFmpeg)"
    pass

@needle.tool
def extract_video_clip(src: str, start: str, duration: str, dst: str = None):
    "Extract a time-range clip from a video file (requires FFmpeg)"
    pass

# ── System control tools ─────────────────────────────────────────────
@needle.tool
def battery_status():
    """Read the computer's remaining battery charge and charging state.

    Use for battery level, battery percentage, remaining battery power, or
    whether the computer is charging. Never use for screen brightness.
    """
    pass

@needle.tool
def lock_screen():
    "Lock the Windows screen"
    pass

@needle.tool
def sleep_system():
    "Put the computer to sleep"
    pass

@needle.tool
def shutdown_system(delay_seconds: int = 0):
    "Shut down the computer"
    pass

@needle.tool
def restart_system(delay_seconds: int = 0):
    "Restart the computer"
    pass

@needle.tool
def cancel_shutdown():
    "Cancel a pending scheduled shutdown or restart"
    pass

@needle.tool
def volume_up(steps: int = 2):
    "Increase audio volume from its current level by a number of steps"
    pass

@needle.tool
def volume_down(steps: int = 2):
    "Decrease audio volume from its current level by a number of steps"
    pass

@needle.tool
def mute_volume():
    "Toggle mute on system volume"
    pass

@needle.tool
def get_volume():
    "Get the current system volume level"
    pass

@needle.tool
def set_volume(level: int):
    """Set the system audio speaker volume to an absolute percentage from 0 to 100.

    Use for 'change my volume to 44' or 'set volume to 44 percent'.
    Use volume_up or volume_down for relative changes by a number of steps.
    """
    pass

@needle.tool
def get_brightness():
    """Read the display backlight brightness percentage.

    Use only when the user explicitly asks about screen, display, monitor, or
    brightness. Never use for battery charge or remaining power.
    """
    pass

@needle.tool
def set_brightness(level: int):
    "Set the screen brightness (0-100)"
    pass

@needle.tool
def toggle_dark_mode():
    "Toggle Windows dark/light mode"
    pass

@needle.tool
def set_dark_mode(enable: bool):
    "Enable or disable Windows dark mode explicitly"
    pass

# ── Window management tools ──────────────────────────────────────────
@needle.tool
def get_open_windows():
    "List all currently open windows"
    pass

@needle.tool
def minimize_all_windows():
    "Minimize all windows and show the desktop"
    pass

@needle.tool
def focus_window(title_fragment: str):
    "Bring a window to the foreground by title"
    pass

# ── Task management tools ────────────────────────────────────────────
@needle.tool
def add_task(title: str, due_date: str = None, priority: str = "normal"):
    "Add a new to-do task"
    pass

@needle.tool
def list_tasks(show_all: bool = False):
    "List pending or all tasks"
    pass

@needle.tool
def complete_task(task_id: int):
    "Mark a task as completed by its ID"
    pass

@needle.tool
def remove_task(task_id: int):
    "Delete a task by its ID"
    pass

@needle.tool
def clear_completed_tasks():
    "Remove all completed tasks from the list"
    pass

# ── Notes tools ────────────────────────────────────────────────────
@needle.tool
def create_note(title: str, content: str, tags: list = None):
    """Save a new note as a TXT file and open it in Notepad.

    Use for requests like "make a note", "add a note", "remember that...",
    "write a note in Notepad", or "buat catatan di Notepad".
    Keep a searchable copy with optional tags. Use add_task for to-do tasks
    and write_document for documents with an explicit output path or format.
    """
    pass

@needle.tool
def search_notes(query: str):
    """Search previously saved notes by title, content, or tag.

    Use for "find my note about...", "what did I note about...", or
    "search my notes for...".
    """
    pass

@needle.tool
def search_conversation_history(query: str = "", scope: str = "all"):
    """Search saved conversation history for previous user messages and Ember responses.

    Retrieve past discussions, requests, and reported outcomes across sessions.
    query contains topic words; empty query lists recent conversations.
    Returns up to five historical conversation excerpts.
    """
    pass

# ── Multi-document / synthesis tools ─────────────────────────────────
@needle.tool
def create_spreadsheet(path: str, rows: list, sheet_name: str = "Sheet1", open_after: bool = True):
    """Create an Excel XLSX workbook from rows and open it in the spreadsheet app.

    Use for "create an Excel spreadsheet" or "buat tabel Excel".
    rows is a list of lists, e.g. [["Item", "Quantity"], ["Milk", 2]].
    path is a new .xlsx file; sheet_name defaults to Sheet1.
    """
    pass

@needle.tool
def create_google_doc(title: str, content: str, open_after: bool = True):
    """Create a Google Docs document online, insert content, and open its browser link.

    Use for "create a Google Docs document" or "buat dokumen di Google Docs".
    Requires a previously connected Google account. title is the document title,
    content is its text body. Use write_document for a local Word DOCX file.
    """
    pass

@needle.tool
def batch_read_folder(folder: str, extensions: list = None, max_chars_per_file: int = 4000):
    "Read all readable documents in a folder (PDF, DOCX, XLSX, PPTX, TXT, CSV, Markdown, etc.) and return their combined text content"
    pass

@needle.tool
def folder_explain(folder: str):
    "Return a structured description of what a folder contains: file counts by type, total size, and the five largest files"
    pass

@needle.tool
def write_document(path: str, content: str, fmt: str = None):
    "Write text content to a local file. Supports TXT, Markdown (.md), PDF, DOCX, and XLSX (content as comma-separated CSV rows). Use create_google_doc for online Google Docs."
    pass


ALL_TOOLS = [
    run_shell, read_file, write_file, list_dir, get_clipboard, set_clipboard,
    open_file, search_web, get_active_window, close_window, get_system_info,
    kill_process, find_files, organize_folder, move_file, copy_file, rename_file,
    delete_file, get_file_info, create_directory, disk_usage, ram_status,
    running_processes, system_uptime, cpu_info, cpu_temperature, launch_app,
    summarize_file, analyze_files, grep_file, grep_folder, diff_files,
    extract_patterns, compress_to_zip, extract_archive, list_archive_contents,
    find_large_files, find_old_files, find_duplicate_files, take_screenshot,
    resize_image, convert_image, rotate_image, get_image_info, extract_audio,
    extract_video_clip, battery_status, lock_screen, sleep_system,
    shutdown_system, restart_system, cancel_shutdown, volume_up, volume_down,
    mute_volume, get_volume, set_volume, get_brightness, set_brightness, toggle_dark_mode,
    set_dark_mode, get_open_windows, minimize_all_windows, focus_window,
    add_task, list_tasks, complete_task, remove_task, clear_completed_tasks,
    create_note, search_notes, search_conversation_history,
    batch_read_folder, folder_explain, write_document, create_spreadsheet, create_google_doc,
]

needle_agent = needle.Needle(tools=ALL_TOOLS, tool_index_path="tools.idx")
CONFIDENCE_THRESHOLD = 0.5  # diturunin dari 0.7 -- banyak match yang BENER
# konsisten nyangkut di 0.4-0.65 (netflix 0.55, volume_down 0.537, list_files
# 0.40, open_app 0.50). 0.7 kebesaran buat kombinasi 71 tool + model 45M ini.


def _user_folder_path(folder_name: str) -> str:
    """Resolve Windows' configured known folder, with a portable fallback."""
    registry_values = {
        "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
        "Documents": "Personal",
        "Desktop": "Desktop",
    }
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as key:
                value, _ = winreg.QueryValueEx(key, registry_values[folder_name])
            return os.path.expandvars(value)
        except (FileNotFoundError, OSError):
            pass
    return str(Path.home() / folder_name)


def _document_intent_call(query: str) -> dict | None:
    """Preserve explicit quoted content and JSON rows that Needle can misparse.

    Only complete, affirmative creation commands match. Free-form requests
    continue through Needle; this parser does not guess missing content.
    """
    quoted = r'''(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')'''

    def unquote(value: str) -> str:
        if value.startswith('"'):
            try:
                return json.loads(value)
            except ValueError:
                pass
        return value[1:-1]

    note_prefix = (
        r"(?:create (?:a )?note(?: in notepad)? (?:titled|called)|"
        r"buat catatan(?: di notepad)? (?:dengan judul|berjudul))"
    )
    google_prefix = (
        r"(?:create (?:a )?google docs document (?:titled|called)|"
        r"buat dokumen (?:di )?google docs (?:dengan judul|berjudul))"
    )
    for name, prefix, suffix in (
        ("create_note", note_prefix, r"(?: in notepad)?"),
        ("create_google_doc", google_prefix, ""),
    ):
        match = re.fullmatch(
            rf"{prefix}\s+(?P<title>{quoted})\s+(?:with content|dan isi|dengan isi)\s+"
            rf"(?P<content>{quoted}){suffix}\s*[.!]?",
            query.strip(), re.IGNORECASE | re.DOTALL,
        )
        if match:
            return {"name": name, "arguments": {
                "title": unquote(match["title"]), "content": unquote(match["content"])
            }}

    spreadsheet = re.fullmatch(
        rf"(?:create (?:an? )?(?:excel )?spreadsheet|buat (?:tabel|spreadsheet|file) excel)\s+"
        rf"(?:at|di)\s+(?P<path>{quoted})\s+(?:with rows|dengan (?:baris|data))\s+"
        r"(?P<rows>\[.*\])\s*[.]?",
        query.strip(), re.IGNORECASE | re.DOTALL,
    )
    if spreadsheet:
        # A quoted Windows path is not a JSON string: backslashes stay literal.
        path = spreadsheet["path"][1:-1]
        if Path(path).suffix.lower() == ".xlsx":
            rows = json.loads(spreadsheet["rows"])
            return {"name": "create_spreadsheet", "arguments": {"path": path, "rows": rows}}
    return None


def _known_intent_call(query: str) -> dict | None:
    """Handle explicit document commands and narrow OS phrases Needle confuses.

    Document commands must include their content; other shortcuts resolve
    read-only requests. General tool selection still goes through Needle.
    """
    document_call = _document_intent_call(query)
    if document_call:
        return document_call

    normalized = " ".join(query.lower().split())

    # A path plus an explicit "summarize" verb is already a complete,
    # unambiguous local-file request. Needle's small router model scores this
    # combination poorly (especially with spaces in the path), so preserve
    # the exact path for aliasing. Execution still requires model validation.
    # Handles both a quoted path ("...") and a bare absolute Windows path
    # (unambiguous on its own since it starts with a drive letter).
    summary_match = re.search(
        r"\b(?:summari[sz](?:e|ing|ed)?|summary|ringkas|rangkum)\b",
        query,
        re.IGNORECASE,
    )

    _DOC_EXT = r"pdf|docx|xlsx|pptx|txt|md|csv"

    quoted_path_match = re.search(
        rf'''["']([^"']+\.(?:{_DOC_EXT}))["']''',
        query,
        re.IGNORECASE,
    )

    bare_path_match = re.search(
        rf'([A-Za-z]:\\[^"\n]+?\.(?:{_DOC_EXT}))(?=\s|$|[.,!?])',
        query,
        re.IGNORECASE,
    )

    path_match = quoted_path_match or bare_path_match

    if summary_match and path_match:
        return {
            "name": "summarize_file",
            "arguments": {
                "path": path_match.group(1).strip()
            },
        }

    if re.search(r"\bbattery\b", normalized) and re.search(
        r"\b(level|percentage|percent|charge|charged|charging|status|power)\b",
        normalized,
    ):
        return {"name": "battery_status", "arguments": {}}

    list_request = re.search(r"\b(list|show|display|what(?:'s| is| are)?|see)\b", normalized)
    files_request = re.search(r"\b(files?|folders?|contents?|downloads?)\b", normalized)
    if list_request and files_request:
        folders = {
            "downloads": "Downloads",
            "documents": "Documents",
            "desktop": "Desktop",
        }
        for keyword, folder_name in folders.items():
            if re.search(rf"\b{keyword}\b", normalized):
                return {
                    "name": "list_dir",
                    "arguments": {"path": _user_folder_path(folder_name)},
                }

    if re.search(r"\b(show|list)\b.*\b(tasks?|to-?dos?)\b", normalized):
        return {"name": "list_tasks", "arguments": {"show_all": False}}

    return None


def _execute_call(call: dict, tool_registry, confidence: float | None = None) -> dict:
    """Execute one Needle-compatible call and keep the CLI response shape."""
    exec_result = tool_registry.execute_tool(call["name"], call["arguments"])
    result = {
        "route": "tool_call",
        "tool": call["name"],
        "arguments": call["arguments"],
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


def route_and_execute(query: str, tool_registry, *, diagnostics: dict | None = None):
    """
    Route query lewat Needle. Kalau match & confident -> eksekusi beneran
    lewat ToolRegistry asli EmberOS. Kalau tidak ada tool yang cocok ->
    kembalikan respons OS-agent yang aman tanpa generasi LLM.

    tool_registry: instance dari emberos.tools.ToolRegistry (real EmberOS)
    """
    history_requested = False
    if getattr(tool_registry, "memory", None) is not None:
        from emberos.history_routing import select_history

        selected = select_history(query)
        if diagnostics is not None:
            diagnostics["history_selection"] = {"model_input": query, "raw_model_result": selected}
        validation = selected.get("validation") or {}
        proposals = selected.get("function_calls") or []
        # The small view may propose live-data tools, but is never allowed to
        # execute them. A history choice only prevents conflicting live actions;
        # the main model must still select the tool and supply its arguments.
        history_proposed = any(call.get("name") == "search_conversation_history" for call in proposals)
        if history_proposed:
            if validation.get("negation"):
                return {"route": "no_action", "needle_confidence": selected.get("confidence"),
                        "response": "No action taken.", "reason": "negated_request", "truncated": False}
            confidence = selected.get("confidence")
            if (selected.get("success") is not True or selected.get("error")
                    or validation.get("ungrounded") or len(proposals) != 1
                    or proposals[0].get("arguments") != {}
                    or confidence is None or confidence < CONFIDENCE_THRESHOLD):
                return {"route": "unresolved_tool_request", "needle_confidence": confidence,
                        "response": "I couldn't reliably interpret this conversation-history request.",
                        "reason": "uncertain_history_request", "truncated": False}
            history_requested = True

    known_call = _known_intent_call(query)
    model_query = query
    path_alias = None
    if known_call and known_call["name"] == "summarize_file":
        original_path = known_call["arguments"]["path"]
        alias = "document" + Path(original_path).suffix.lower()
        # Keep long OS paths outside the small router's generation context.
        # This substitutes only the supplied file reference; the surrounding
        # request, including negation, remains unchanged for model selection.
        if original_path in query and alias not in query.replace(original_path, "", 1):
            model_query = query.replace(original_path, alias, 1)
            path_alias = alias
    needle_agent.reset()
    result = needle_agent.complete(model_query)
    if diagnostics is not None:
        diagnostics.update(model_input=model_query, raw_model_result=result)

    def unresolved(message, reason):
        return {"route": "unresolved_tool_request", "needle_confidence": result.get("confidence"),
                "response": message, "reason": reason, "truncated": False}

    if result.get("success") is False or result.get("error"):
        return unresolved("I couldn't finish interpreting that request. Please try again.", "model_error")
    validation = result.get("validation") or {}
    if validation.get("negation"):
        return {"route": "no_action", "needle_confidence": result.get("confidence"),
                "response": "No action taken.", "reason": "negated_request", "truncated": False}
    if validation.get("ungrounded"):
        return unresolved("I couldn't reliably determine the tool arguments from your message. Please state the target and values explicitly.",
                          "ungrounded_arguments")

    calls = result.get("function_calls") or []
    if history_requested and (len(calls) != 1 or calls[0].get("name") != "search_conversation_history"):
        return unresolved("I couldn't reliably interpret this conversation-history request.", "conflicting_history_route")
    if len(calls) > 1:
        return unresolved("This request needs multiple actions. Please ask for one action at a time for now.", "multiple_actions")
    has_match = bool(calls)
    confident = (
        result["confidence"] is not None
        and result["confidence"] >= CONFIDENCE_THRESHOLD
    )

    if has_match and not confident:
        # Needle found a tool, but its calibrated confidence says not to act.
        # SmolLM2 cannot inspect the OS, so generating an answer here would
        # fabricate system state instead of safely declining the tool call.
        return {
            "route": "unresolved_tool_request",
            "needle_confidence": result["confidence"],
            "needle_reasoning": result.get("reasoning"),
            "reason": "low_confidence",
            "response": (
                "I couldn't reliably identify the right system action. "
                "Please rephrase the request with the device feature or folder name."
            ),
            "truncated": False,
        }

    if not has_match:
        from smollm_fallback import run_fallback
        text, truncated = run_fallback(query)
        return {
            "route": "fallback_llm",
            "needle_confidence": result["confidence"],
            "needle_reasoning": result.get("reasoning"),
            "response": text,
            "truncated": truncated,
        }

    call = calls[0]
    # Existing exact-content parsers may preserve quoted paths/JSON only after
    # the model selects the same tool and passes confidence/native validation.
    # They no longer bypass the model or manufacture confidence=1.0.
    if known_call and known_call["name"] == call.get("name"):
        if path_alias and call.get("arguments", {}).get("path") != path_alias:
            return unresolved("I couldn't match the selected document to your file. Please state the file path again.", "unmatched_file_reference")
        call = known_call
    try:
        tool_registry.validate_arguments(call.get("name"), call.get("arguments"))
    except (TypeError, ValueError) as exc:
        return unresolved(f"I need valid tool arguments before I can continue: {exc}", "invalid_arguments")
    return _execute_call(call, tool_registry, confidence=result["confidence"])
