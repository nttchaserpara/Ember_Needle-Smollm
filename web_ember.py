"""
web_ember.py

Lightweight, secure, zero-dependency Web GUI for EmberOS.
Theme: Dark & Orange Chatroom Interface with clean responses and translucent
collapsible benchmark dropdowns under every assistant turn.

Security Controls:
  - Token-based Authentication (X-Ember-Token header)
  - Web Tool Policy (safe allowlist, confirmation gate, permanent RCE block)
  - Safe Bind Address (127.0.0.1 default, --lan opt-in for LAN access)
  - CSRF & Cross-Origin Protection
  - Resource & Payload Limits (max 64 KB body, rate limiting per IP)
  - Audit Logging to logs/web_access.log

Usage:
  python web_ember.py              # Localhost only (127.0.0.1:8080)
  python web_ember.py --lan        # LAN access (0.0.0.0:8080)
  python web_ember.py --port 9000  # Custom port
"""

import argparse
from collections import defaultdict
import datetime
import html
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import platform
import psutil
import secrets
import sqlite3
import sys
import threading
import time
import urllib.parse
import webbrowser

sys.path.insert(0, ".")

from needle_router import route_and_execute
from emberos.tools import ToolRegistry, ToolResult
from emberos.benchmark import RequestMemorySampler
from emberos.responses import format_tool_response
from emberos.config import ROOT_DIR
from emberos.memory import ConversationMemory
from emberos.replies import ReplyRenderer, original_response
from run_ember import handle_request, _format_bytes, _memory_snapshot

_PROCESS = psutil.Process(os.getpid())

# ---------------------------------------------------------------------------
# Security: Web-Safe Tool Allowlist
# ---------------------------------------------------------------------------
WEB_SAFE_TOOLS = {
    # System monitoring (read-only)
    "disk_usage", "ram_status", "running_processes", "system_uptime",
    "cpu_info", "cpu_temperature", "battery_status", "get_open_windows",
    "get_volume", "get_brightness", "list_tasks", "get_file_info",
    "find_files", "find_large_files", "find_old_files", "find_duplicate_files",
    "get_image_info", "list_archive_contents", "search_notes",
    "search_conversation_history", "folder_explain", "get_system_info",
    # Safe file and document operations
    "read_file", "list_dir", "copy_file", "create_directory",
    "list_archive_contents", "extract_archive", "compress_to_zip",
    "write_file", "write_document", "batch_read_folder",
    "summarize_file", "analyze_files", "grep_file", "grep_folder",
    "diff_files", "extract_patterns",
    # Safe desktop controls and app actions
    "set_volume", "volume_up", "volume_down", "mute_volume",
    "set_brightness", "toggle_dark_mode", "set_dark_mode",
    "launch_app", "open_file", "focus_window", "get_active_window",
    "minimize_all_windows", "get_open_windows", "cancel_shutdown",
    # Task, notes, spreadsheets, and connected document creation
    "add_task", "complete_task", "remove_task", "list_tasks",
    "create_note", "search_notes", "create_spreadsheet", "create_google_doc",
    # Media
    "resize_image", "convert_image", "rotate_image", "get_image_info",
    "take_screenshot", "extract_audio", "extract_video_clip",
    # Undo
    "undo_last_action",
}

# Destructive tools are allowed in Web mode only after handle_request obtains
# the exact confirmation words. cancel_shutdown is intentionally safe: it
# prevents a pending shutdown instead of causing data loss.
WEB_CONFIRM_TOOLS = {
    "delete_file", "move_file", "rename_file", "organize_folder",
    "clear_completed_tasks", "kill_process", "shutdown_system",
    "restart_system", "sleep_system", "lock_screen",
}

# RCE remains permanently unavailable in every GUI path.
WEB_BLOCKED_ALWAYS = {"run_shell"}

MAX_BODY_SIZE = 64 * 1024  # 64 KB max payload
RATE_LIMIT_PER_MINUTE = 40  # Max requests per IP per minute

# Audit log path
AUDIT_LOG_FILE = ROOT_DIR / "logs" / "web_access.log"
AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def _log_web_audit(client_ip: str, endpoint: str, query: str, tool: str, status_code: int, extra: str = ""):
    """Record access logs with caller IP for security auditing."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    clean_query = query.replace("\n", " ")[:120] if query else ""
    log_line = f"[{now}] IP={client_ip:<15} HTTP={status_code} ENDPOINT={endpoint:<12} TOOL={tool:<18} QUERY={clean_query!r} {extra}\n"
    try:
        with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_line)
    except OSError:
        pass


class RateLimiter:
    """Thread-safe sliding window rate limiter per client IP."""
    def __init__(self, max_per_minute=RATE_LIMIT_PER_MINUTE):
        self.max_per_minute = max_per_minute
        self.requests = defaultdict(list)
        self.lock = threading.Lock()

    def is_allowed(self, ip: str) -> bool:
        now = time.monotonic()
        with self.lock:
            timestamps = self.requests[ip]
            # Prune timestamps older than 60s
            self.requests[ip] = [t for t in timestamps if now - t < 60.0]
            if len(self.requests[ip]) >= self.max_per_minute:
                return False
            self.requests[ip].append(now)
            return True


# ---------------------------------------------------------------------------
# Security: web-safe ToolRegistry
# ---------------------------------------------------------------------------
class WebSafeToolRegistry(ToolRegistry):
    """Enforce the Web policy before any registered function is called."""

    @staticmethod
    def _restricted(name: str, *, always: bool = False) -> ToolResult:
        if always:
            error = f"'{name}' is permanently restricted in Web mode."
        else:
            error = f"'{name}' is restricted in Web mode."
        return ToolResult(success=False, error=error, status="unsupported")

    def execute_tool(self, name: str, params: dict):
        if name in WEB_BLOCKED_ALWAYS:
            return self._restricted(name, always=True)
        if name not in WEB_SAFE_TOOLS and name not in WEB_CONFIRM_TOOLS:
            return self._restricted(name)
        # Direct calls cannot bypass the confirmation boundary. The normal
        # Web request path reaches this class through run_ember's gate, and a
        # confirmed replay uses execute_confirmed() below.
        if name in WEB_CONFIRM_TOOLS:
            return ToolResult(
                success=False,
                error=f"'{name}' requires explicit confirmation in Web mode.",
                status="unsupported",
            )
        return super().execute_tool(name, params)

    def execute_confirmed(self, name: str, params: dict):
        if name in WEB_BLOCKED_ALWAYS:
            return self._restricted(name, always=True)
        if name not in WEB_SAFE_TOOLS and name not in WEB_CONFIRM_TOOLS:
            return self._restricted(name)
        return super().execute_tool(name, params)

    def execute_tool_chain(self, calls: list[dict]):
        invalid = [
            call for call in calls
            if call.get("name") in WEB_BLOCKED_ALWAYS
            or call.get("name") not in WEB_SAFE_TOOLS
            or call.get("name") in WEB_CONFIRM_TOOLS
        ]
        if invalid:
            results = []
            invalid_names = {call.get("name") for call in invalid}
            for call in calls:
                name = call.get("name")
                params = call.get("arguments") or {}
                if name in invalid_names:
                    result = self._restricted(name, always=name in WEB_BLOCKED_ALWAYS)
                    results.append({"route": "tool_call", "tool": name,
                                    "arguments": params, **result.to_dict()})
                else:
                    results.append({"route": "skipped", "tool": name,
                                    "arguments": params})
            return results, None
        return super().execute_tool_chain(calls)

    def execute_confirmed_chain(self, calls: list[dict]):
        invalid = [
            call for call in calls
            if call.get("name") in WEB_BLOCKED_ALWAYS
            or call.get("name") not in WEB_SAFE_TOOLS | WEB_CONFIRM_TOOLS
        ]
        if invalid:
            results = []
            invalid_names = {call.get("name") for call in invalid}
            for call in calls:
                name = call.get("name")
                params = call.get("arguments") or {}
                if name in invalid_names:
                    result = self._restricted(name, always=name in WEB_BLOCKED_ALWAYS)
                    results.append({"route": "tool_call", "tool": name,
                                    "arguments": params, **result.to_dict()})
                else:
                    results.append({"route": "skipped", "tool": name,
                                    "arguments": params})
            return results, None
        return super().execute_tool_chain(calls)


# ---------------------------------------------------------------------------
# Global Application State
# ---------------------------------------------------------------------------
class EmberWebContext:
    def __init__(self, auth_token: str):
        self.auth_token = auth_token
        self.rate_limiter = RateLimiter()
        self.memory = None
        if os.environ.get("EMBER_MEMORY", "1") != "0":
            try:
                self.memory = ConversationMemory(ROOT_DIR / "data" / "conversation.sqlite3")
            except (OSError, sqlite3.Error, ValueError) as exc:
                print(f"[memory] Conversation memory is unavailable: {exc}")
        self.registry = WebSafeToolRegistry(memory=self.memory)
        self.reply_renderer = ReplyRenderer()
        self.multistep_enabled = True  # Natural multi-step enabled by default


# ---------------------------------------------------------------------------
# HTML / CSS / JS Single Page Application (Dark & Orange Theme)
# ---------------------------------------------------------------------------
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ZEVION Labs</title>
  <style>
    :root {
      --bg-base: #0c0e12;
      --bg-surface: #14171e;
      --bg-card: #1c2028;
      --border-subtle: #272c38;
      --border-highlight: #ff6b3544;
      --orange-primary: #ff6b35;
      --orange-hover: #ff824d;
      --orange-glow: rgba(255, 107, 53, 0.25);
      --orange-dim: #993d1a;
      --text-main: #f0f2f6;
      --text-muted: #8a94a6;
      --text-dim: #5c6475;
      --user-bubble: #1f2533;
      --ember-bubble: #171b24;
      --success: #10b981;
      --error: #ef4444;
      --warning: #f59e0b;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg-base);
      color: var(--text-main);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      display: flex;
      flex-direction: column;
      height: 100vh;
      overflow: hidden;
    }

    /* Header */
    header {
      background: var(--bg-surface);
      border-bottom: 1px solid var(--border-subtle);
      padding: 10px 24px;
      z-index: 10;
      transition: opacity 0.3s ease, transform 0.3s ease;
    }
    header.header-hidden {
      display: none;
    }
    .header-inner {
      display: flex;
      align-items: center;
      justify-content: space-between;
      max-width: 820px;
      margin: 0 auto;
      width: 100%;
    }
    .header-logo {
      height: 28px;
      width: auto;
      object-fit: contain;
      mix-blend-mode: lighten;
      filter: brightness(2) contrast(0.8);
    }
    .brand {
      display: flex;
      align-items: center;
      min-width: 0;
    }
    .header-status {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 0.75rem;
      color: var(--text-muted);
      margin-left: 12px;
    }
    .header-status .dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--success);
      box-shadow: 0 0 4px var(--success);
    }
    .header-right {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .header-btn {
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      color: var(--text-muted);
      padding: 6px 8px;
      border-radius: 8px;
      cursor: pointer;
      transition: all 0.15s;
      display: flex;
      align-items: center;
    }
    .header-btn:hover {
      border-color: var(--orange-primary);
      color: var(--orange-hover);
    }

    /* Welcome screen */
    #welcome-screen {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      flex: 1;
      padding: 40px 24px;
      gap: 24px;
      transition: opacity 0.3s ease, transform 0.3s ease;
    }
    #welcome-screen.hidden {
      opacity: 0;
      transform: translateY(-20px);
      pointer-events: none;
      display: none;
    }
    .welcome-logo {
      height: clamp(48px, 8vw, 80px);
      width: auto;
      object-fit: contain;
      mix-blend-mode: lighten;
      filter: brightness(2) contrast(0.8);
    }
    .welcome-status {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 0.85rem;
      color: var(--text-muted);
    }
    .welcome-status .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--success);
      box-shadow: 0 0 6px var(--success);
    }
    .welcome-input-area {
      width: 100%;
      max-width: 680px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .welcome-form {
      display: flex;
      gap: 12px;
      align-items: flex-end;
    }
    .welcome-quick-btns {
      display: flex;
      gap: 10px;
      justify-content: center;
      flex-wrap: wrap;
    }
    .welcome-quick-btn {
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      color: var(--text-muted);
      font-family: inherit;
      font-size: 0.8rem;
      padding: 8px 16px;
      border-radius: 20px;
      cursor: pointer;
      transition: all 0.15s;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .welcome-quick-btn:hover {
      border-color: var(--orange-primary);
      color: var(--orange-hover);
      background: rgba(255, 107, 53, 0.08);
    }

    /* Main Chat Container */
    #chat-container {
      flex: 1;
      overflow-y: auto;
      padding: 24px 20px;
      display: flex;
      flex-direction: column;
      gap: 18px;
      scroll-behavior: smooth;
    }
    #chat-container.chat-hidden,
    footer.footer-hidden {
      display: none;
    }
    #chat-container::-webkit-scrollbar {
      width: 4px;
    }
    #chat-container::-webkit-scrollbar-track {
      background: transparent;
    }
    #chat-container::-webkit-scrollbar-thumb {
      background: var(--border-subtle);
      border-radius: 4px;
    }
    #chat-container::-webkit-scrollbar-thumb:hover {
      background: var(--orange-dim);
    }

    .msg-wrapper {
      display: flex;
      flex-direction: column;
      max-width: 820px;
      width: 100%;
      margin: 0 auto;
      animation: fadeIn 0.25s ease-out;
    }
    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(6px); }
      to { opacity: 1; transform: translateY(0); }
    }

    .msg-user {
      align-self: flex-end;
      align-items: flex-end;
    }
    .msg-user .bubble {
      background: var(--user-bubble);
      border: 1px solid var(--border-highlight);
      border-radius: 18px 18px 4px 18px;
      padding: 12px 18px;
      color: var(--text-main);
      font-size: 0.95rem;
      line-height: 1.5;
      max-width: 85%;
      box-shadow: 0 4px 12px rgba(0,0,0,0.2);
    }

    .msg-ember {
      align-self: flex-start;
      align-items: flex-start;
    }
    .msg-ember .bubble {
      background: var(--ember-bubble);
      border: 1px solid var(--border-subtle);
      border-left: 3px solid var(--orange-primary);
      border-radius: 4px 18px 18px 18px;
      padding: 14px 20px;
      color: var(--text-main);
      font-size: 0.95rem;
      line-height: 1.6;
      width: 100%;
      box-shadow: 0 4px 16px rgba(0,0,0,0.25);
      position: relative;
    }
    .msg-timestamp {
      display: block;
      font-size: 0.68rem;
      color: var(--text-dim);
      text-align: right;
      margin-top: 6px;
      opacity: 0.7;
    }
    .copy-btn {
      position: absolute;
      top: 10px;
      right: 10px;
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      color: var(--text-dim);
      font-family: inherit;
      font-size: 0.68rem;
      padding: 3px 8px;
      border-radius: 6px;
      cursor: pointer;
      opacity: 0;
      transition: opacity 0.15s, color 0.15s;
    }
    .msg-ember .bubble:hover .copy-btn {
      opacity: 1;
    }
    .copy-btn:hover {
      color: var(--orange-hover);
      border-color: var(--orange-primary);
    }
    .bubble pre {
      background: #090b0e;
      border: 1px solid var(--border-subtle);
      padding: 10px 14px;
      border-radius: 8px;
      font-family: Consolas, Monaco, "Courier New", monospace;
      font-size: 0.85rem;
      overflow-x: auto;
      margin: 8px 0;
      color: #e5e7eb;
    }
    .bubble code {
      font-family: Consolas, Monaco, monospace;
      background: rgba(255,107,53,0.1);
      color: #ff9f43;
      padding: 2px 5px;
      border-radius: 4px;
      font-size: 0.88rem;
    }

    /* Translucent Benchmark Dropdown */
    .meta-dropdown {
      margin-top: 8px;
      background: rgba(20, 24, 32, 0.45);
      border: 1px solid rgba(255, 107, 53, 0.15);
      backdrop-filter: blur(8px);
      border-radius: 8px;
      font-size: 0.78rem;
      color: var(--text-dim);
      overflow: hidden;
      transition: all 0.2s ease;
      width: 100%;
    }
    .meta-dropdown:hover {
      border-color: rgba(255, 107, 53, 0.35);
      background: rgba(20, 24, 32, 0.65);
    }
    .meta-summary {
      padding: 6px 12px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: space-between;
      user-select: none;
      color: var(--text-muted);
    }
    .meta-summary:hover {
      color: var(--orange-hover);
    }
    .meta-summary::-webkit-details-marker { display: none; }
    .meta-pills {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }
    .pill.accent { color: var(--orange-primary); font-weight: 600; }
    .meta-details-content {
      padding: 10px 14px;
      border-top: 1px solid rgba(255, 255, 255, 0.05);
      background: rgba(12, 14, 18, 0.6);
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 8px;
      font-size: 0.75rem;
      color: var(--text-muted);
    }
    .stat-item {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    .stat-label { color: var(--text-dim); font-size: 0.7rem; text-transform: uppercase; }
    .stat-val { color: var(--text-main); font-weight: 500; }

    /* Typing indicator */
    .typing {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 10px 14px;
    }
    .typing .dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--orange-primary);
      animation: pulse 1.2s infinite;
    }
    .typing .dot:nth-child(2) { animation-delay: 0.2s; }
    .typing .dot:nth-child(3) { animation-delay: 0.4s; }
    @keyframes pulse {
      0%, 100% { opacity: 0.3; transform: scale(0.8); }
      50% { opacity: 1; transform: scale(1.1); }
    }

    /* Quick Actions */
    .quick-actions-wrapper {
      max-width: 820px;
      width: 100%;
      margin: 0 auto;
      padding: 0 20px 8px 20px;
      display: flex;
      justify-content: flex-start;
      position: relative;
    }
    .quick-actions-btn {
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      color: var(--text-muted);
      font-family: inherit;
      font-size: 0.85rem;
      font-weight: 600;
      padding: 8px 20px;
      border-radius: 20px;
      cursor: pointer;
      transition: all 0.15s;
      letter-spacing: 0.3px;
    }
    .quick-actions-btn:hover,
    .quick-actions-btn.active {
      border-color: var(--orange-primary);
      color: var(--orange-hover);
      background: rgba(255, 107, 53, 0.08);
    }
    .quick-actions-panel {
      position: fixed;
      bottom: 90px;
      left: 20px;
      right: 20px;
      width: auto;
      max-width: 820px;
      margin: 0 auto;
      background: var(--bg-card);
      border: 1px solid var(--border-highlight);
      border-radius: 12px;
      padding: 12px;
      box-shadow: 0 -8px 32px rgba(0,0,0,0.4);
      backdrop-filter: blur(8px);
      z-index: 100;
      animation: slideUp 0.15s ease-out;
    }
    @keyframes slideUp {
      from { opacity: 0; transform: translateY(8px); }
      to { opacity: 1; transform: translateY(0); }
    }
    .quick-actions-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
      gap: 8px;
    }
    .qa-item {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      color: var(--text-muted);
      font-family: inherit;
      font-size: 0.78rem;
      padding: 8px 12px;
      border-radius: 8px;
      cursor: pointer;
      transition: all 0.12s;
      text-align: left;
    }
    .qa-item:hover {
      border-color: var(--orange-primary);
      color: var(--orange-hover);
      background: rgba(255, 107, 53, 0.06);
    }
    .qa-item.clear-action {
      color: var(--orange-hover);
      border-color: var(--orange-dim);
    }

    /* Input Area */
    footer {
      background: var(--bg-surface);
      border-top: 1px solid var(--border-subtle);
      padding: 14px 24px;
    }
    .input-form {
      max-width: 820px;
      margin: 0 auto;
      display: flex;
      gap: 12px;
      align-items: flex-end;
    }
    .input-box-wrapper {
      flex: 1;
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      border-radius: 14px;
      padding: 8px 14px;
      display: flex;
      align-items: flex-end;
      gap: 8px;
      transition: border-color 0.2s, box-shadow 0.2s;
    }
    .input-box-wrapper:focus-within {
      border-color: var(--orange-primary);
      box-shadow: 0 0 0 3px var(--orange-glow);
    }
    textarea#user-input,
    textarea#user-input-footer {
      width: 100%;
      background: transparent;
      border: none;
      outline: none;
      color: var(--text-main);
      font-family: inherit;
      font-size: 0.95rem;
      line-height: 1.4;
      resize: none;
      max-height: 120px;
      min-height: 24px;
    }
    .char-counter {
      font-size: 0.65rem;
      color: var(--text-dim);
      align-self: flex-end;
      padding-bottom: 4px;
      white-space: nowrap;
      opacity: 0.6;
    }
    .toast {
      position: fixed;
      bottom: 90px;
      right: 24px;
      background: var(--bg-card);
      border: 1px solid var(--orange-primary);
      color: var(--text-main);
      font-family: inherit;
      font-size: 0.8rem;
      padding: 8px 16px;
      border-radius: 8px;
      box-shadow: 0 4px 16px rgba(0,0,0,0.3);
      z-index: 200;
      animation: fadeInOut 2.5s ease forwards;
    }
    @keyframes fadeInOut {
      0%   { opacity: 0; transform: translateY(8px); }
      15%  { opacity: 1; transform: translateY(0); }
      75%  { opacity: 1; }
      100% { opacity: 0; }
    }
    button#send-btn,
    button#welcome-send-btn {
      background: linear-gradient(135deg, var(--orange-primary), #e65100);
      border: none;
      outline: none;
      color: white;
      padding: 10px 18px;
      border-radius: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s;
      display: flex;
      align-items: center;
      gap: 6px;
      height: 42px;
    }
    button#send-btn:hover:not(:disabled),
    button#welcome-send-btn:hover:not(:disabled) {
      background: linear-gradient(135deg, var(--orange-hover), var(--orange-primary));
      box-shadow: 0 0 14px var(--orange-glow);
      transform: translateY(-1px);
    }
    button#send-btn:disabled,
    button#welcome-send-btn:disabled {
      opacity: 0.4;
      cursor: not-allowed;
    }
  </style>
</head>
<body>

  <header id="main-header" class="header-hidden">
    <div class="header-inner">
      <div class="brand">
      <img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAABQAAAAHZCAYAAADZmpYDAAAQAElEQVR4AeydB5wcZfnH39ne93rL5e5SSCBUiYoUMZTQe7XwVwEV6QjSRXoREFAUEQERAemidAWMDQQNKOUgySW5Sy6XXHJ1e5//79nchs1l727L7O3s7HOfeW5mZ97yPN+3PzM7qxP8xwSYABNgAkyACTABJsAEmAATYAJMgAlonQDbxwSYQAUTYAdgBRc+m84EmAATYAJMgAkwASZQaQTYXibABJgAE2ACTKASCbADsBJLnW1mAkyACTCByibA1jMBJsAEmAATYAJMgAkwASZQUQTYAVhRxc3GMoFPCfARE2ACTIAJMAEmwASYABNgAkyACTABJqB9AmQhOwCJAgsTYAJMgAkwASbABJgAE2ACTIAJMAHtEmDLmAATqHAC7ACs8ArA5jMBJsAEmAATYAJMgAlUCgG2kwkwASbABJgAE6hUAuwArNSSZ7uZABNgAkygMgmw1UyACTABJsAEmAATYAJMgAlUHAF2AFZckbPBTEAIZsAEmAATYAJMgAkwASbABJgAE2ACTIAJaJ9AykJ2AKZI8J4JMAEmwASYABNgAkyACTABJsAEmID2CLBFTIAJMAHBDkCuBEyACTABJsAEmAATYAJMQPME2EAmwASYABNgAkygkgmwA7CSS59tZwJMgAkwgcoiwNYyASbABJgAE2ACTIAJMAEmUJEE2AFYkcXORlcyAbadCTABJsAEmAATYAJMgAkwASbABJgAE9A+gXQL2QGYToOPmQATYAJMgAkwASbABJgAE2ACTIAJaIcAW8IEmAATSBJgB2ASA/9jAkyACTABJsAEmAATYAJaJcB2MQEmwASYABNgApVOgB2AlV4D2H4mwASYABOoDAJsJRNgAkyACTABJsAEmAATYAIVS4AdgBVb9Gx4JRJgm5kAE2ACTIAJMAEmwASYABNgAkyACTAB7RMYbyE7AMcT4c9MgAkwASbABJgAE2ACTIAJMAEmwATKnwBbwASYABPYQoAdgFtQ8AETYAJMgAkwASbABJgAE9AaAbaHCTABJsAEmAATYAJCsAOQawETYAJMgAkwAa0TYPuYABNgAkyACTABJsAEmAATqGgC7ACs6OJn4yuJANvKBJgAE2ACTIAJMAEmwASYABNgAkyACWifQCYL2QGYiQqfYwJMgAkwASbABJgAE2ACTIAJMAEmUL4EWHMmwASYwFYE2AG4FQ7+wASYABNgAkyACTABJsAEtEKA7WACTIAJMAEmwASYwGYC7ADczIH/MwEmwASYABPQJgG2igkwASbABJgAE2ACTIAJMIGKJ8AOwIqvAgygEgiwjUyACTABJsAEmAATYAJMgAkwASbABJiA9glMZCE7ACciw+eZABNgAkyACTABJsAEmAATYAJMgAmUHwHWmAkwASawDQF2AG6DhE8wASbABJgAE2ACTIAJMIFyJ8D6MwEmwASYABNgAkzgUwLsAPyUBR8xASbABJgAE9AWAbaGCTABJsAEmAATYAJMgAkwASYAAuwABATemICWCbBtTIAJMAEmwASYABNgAkyACTABJsAEmID2CUxmITsAJ6PD15gAE2ACTIAJMAEmwASYABNgAkyACZQPAdaUCTABJpCRADsAM2Lhk0yACTABJsAEmAATYAJMoFwJsN5MgAkwASbABJgAE9iaADsAt+bBn5gAE2ACTIAJaIMAW8EEmAATYAJMgAkwASbABJgAExgjwA7AMRC8YwJaJMA2MQEmwASYABNgAkyACTABJsAEmAATYALaJzCVhewAnIoQX2cCTIAJMAEmwASYABNgAkyACTABJqB+AqwhE2ACTGBCAuwAnBANX2ACTIAJMAEmwASYABNgAuVGgPVlAkyACTABJsAEmMC2BNgBuC0TPsMEmAATYAJMoLwJsPZMgAkwASbABJgAE2ACTIAJMIE0AuwATIPBh0xASwTYFibABJgAE2ACTIAJMAEmwASYABNgAkxA+wSysZAdgNlQ4jBMgAkwASbABJgAE2ACTIAJMAEmwATUS4A1YwJMgAlMSoAdgJPi4YtMgAkwASbABJgAE2ACTKBcCLCeTIAJMAEmwASYABPITIAdgJm58FkmwASYABNgAuVJgLVmAkyACTABJsAEmAATYAJMgAmMI8AOwHFA+CMT0AIBtoEJMAEmwASYABNgAkyACTABJsAEmAAT0D6BbC1kB2C2pDgcE2ACTIAJMAEmwASYABNgAkyACTAB9RFgjZgAE2ACUxJgB+CUiDgAE2ACTIAJMAEmwASYABNQOwHWjwkwASbABJgAE2ACExNgB+DEbPgKE2ACTIAJMIHyIsDaMgEmwASYABNgAkyACTABJsAEMhBgB2AGKHyKCZQzAdadCTABJsAEmAATYAJMgAkwASbABJgAE9A+gVwsZAdgLrQ4LBNgAkyACTABJsAEmAATYAJMgAkwAfUQYE2YABNgAlkRYAdgVpg4EBNgAkyACTABJsAEmAATUCsB1osJMAEmwASYABNgApMTYAfg5Hz4KhNgAkyACTCB8iDAWjIBJsAEmAATYAJMgAkwASbABCYgwA7ACcDwaSZQjgRYZybABJgAE2ACTIAJMAEmwASYABNgAkxA+wRytZAdgLkS4/BMgAkwASbABJgAE2ACTIAJMAEmwARKT4A1YAJMgAlkTYAdgFmj4oBMgAkwASbABJgAE2ACTEBtBFgfJsAEmAATYAJMgAlMTYAdgFMz4hBMgAkwASbABNRNgLVjAkyACTABJsAEmAATYAJMgAlMQoAdgJPA4UtMoJwIsK5MgAkwASbABJgAE2ACTIAJMAEmwASYgPYJ5GMhOwDzocZxmAATYAJMgAkwASbABJgAE2ACTIAJlI4A58wEmAATyIkAOwBzwsWBmQATYAJMgAkwASbABJiAWgiwHkyACTABJsAEmAATyI4AOwCz48ShmAATYAJMgAmokwBrxQSYABNgAkyACTABJsAEmAATmIIAOwCnAMSXmUA5EGAdmQATYAJMgAkwASbABJgAE2ACTIAJMAHtE8jXQnYA5kuO4zEBJsAEmAATYAJMgAkwASbABJgAE5h+ApwjE2ACTCBnAuwAzBkZR2ACTIAJMAEmwASYABNgAqUmwPkzASbABJgAE2ACTCB7AuwAzJ4Vh2QCTIAJMAEmoC4CrA0TYAJMgAkwASbABJgAE2ACTCALAuwAzAISB2ECaibAujEBJsAEmAATYAJMgAkwASbABJgAE2AC2idQiIXsACyEHsdlAkyACTABJsAEmAATYAJMgAkwASYwfQQ4JybABJhAXgTYAZgXNo7EBJgAE2ACTIAJMAEmwARKRYDzZQJMgAkwASbABJhAbgTYAZgbLw7NBJgAE2ACTEAdBFgLJsAEmAATYAJMgAkwASbABJhAlgTYAZglKA7GBNRIgHViAkyACTABJsAEmAATYAJMgAkwASbABLRPoFAL2QFYKEGOzwSYABNgAkyACTABJsAEmAATYAJMoPgEOAcmwASYQN4E2AGYNzqOyASYABNgAkyACTABJsAEppsA58cEmAATYAJMgAkwgdwJsAMwd2YcgwkwASbABJhAaQlw7kyACTABJsAEmAATYAJMgAkwgRwIsAMwB1gclAmoiQDrwgSYABNgAkyACTABJsAEmAATYAJMgAlon4ASFrIDUAmKnAYTYAJMgAkwASbABJgAE2ACTIAJMIHiEeCUmQATYAIFEWAHYEH4ODITYAJMgAkwASbABJgAE5guApwPE2ACTIAJMAEmwATyI8AOwPy4cSwmwASYABNgAqUhwLkyASbABJgAE2ACTIAJMAEmwARyJMAOwByBcXAmoAYCrAMTYAJMgAkwASbABJgAE2ACTIAJMAEmoH0CSlnIDkClSHI6TIAJMAEmwASYABNgAkyACTABJsAElCfAKTIBJsAECibADsCCEXICTIAJMAEmwASYABNgAkyg2AQ4fSbABJgAE2ACTIAJ5E+AHYD5s+OYTIAJMAEmwASmlwDnxgSYABNgAkyACTABJsAEmAATyIMAOwDzgMZRmEApCXDeTIAJMAEmwASYABNgAkyACTABJsAEmID2CShpITsAlaTJaTEBJsAEmAATYAJMgAkwASbABJgAE1COAKfEBJgAE1CEADsAFcHIiTABJsAEmAATYAJMgAkwgWIR4HSZABNgAkyACTABJlAYAXYAFsaPYzMBJsAEmAATmB4CnAsTYAJMgAkwASbABJgAE2ACTCBPAuwAzBMcR2MCpSDAeTIBJsAEmAATYAJMgAkwASbABJgAE2AC2iegtIXsAFSaKKfHBJgAE2ACTIAJMAEmwASYABNgAkygcAKcAhNgAkxAMQLsAFQMJSfEBJgAE2ACTIAJMAEmwASUJsDpMQEmwASYABNgAkygcALsACycIafABJgAE2ACTKC4BDh1JsAEmAATYAJMgAkwASbABJhAAQTYAVgAPI7KBKaTAOfFBJgAE2ACTIAJMAEmwASYABNgAkyACWifQDEsZAdgMahymkyACTABJsAEmAATYAJMgAkwASbABPInwDGZABNgAooSYAegojg5MSbABJgAE2ACTIAJMAEmoBQBTocJMAEmwASYABNgAsoQYAegMhw5FSbABJgAE2ACxSHAqTIBJsAEmAATYAJMgAkwASbABAokwA7AAgFydCYwHQQ4DybABJgAE2ACTIAJMAEmwASYABNgAkxA+wSKZSE7AItFltNlAkyACTABJsAEmAATYAJMgAkwASaQOwGOwQSYABNQnAA7ABVHygky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IIAAAggggAACTSVAANhUp6N9imElCCCAAAIIIIAAAggggAACCCDQ/gKssDUECABb4zxRJQIIIIAAAggggAACCCDQrALUhQACCCDQ5AIEgE1+gigPAQQQQAABBBBoDQGqRAABBBBAAAEEEGhWAQLAZj0z1IUAAgi0ogA1I4AAAggggAACCCCAAAIINJ0AAWDTnZLWL4gVIIAAAggggAACCCCAAAIIIIBA+wuwwtYRIABsnXNFpQgggAACCCCAAAIIIIBAswlQDwIIIIBACwgQALbASaJEBBBAAAEEEECguQWoDgEEEEAAAQQQQKCZBQgAm/nsUBsCCCDQSgLUigACCCCAAAIIIIAAAggg0JQCBIBNeVpatygqRwABBBBAAAEEEEAAAQQQQACB9hdgha0lQADYWueLahFAAAEEEEAAAQQQQACBZhGgDgQQQACBFhEgAGyRE0WZCCCAAAIIIIBAcwpQFQIIIIAAAggggECzCxAANvsZoj4EEECgFQSoEQEEEEAAAQQQQAABBBBAoGkFCACb9tS0XmFUjAACCCCAAAIIIIAAAggggAAC7S/ACltPgACw9c4ZFSOAAAIIIIAAAggggAACSy3A/AgggAACLSRAANhCJ4tSEUAAAQQQQACB5hKgGgQQQAABBBBAAIFWECAAbIWzRI0IIIBAMwtQGwIIIIAAAggggAACCCCAQFMLEAA29elpneKoFAEEEEAAAQQQQAABBBBAAAEE2l+AFbamAAFga543qkYAAQQQQAABBBBAAAEElkqAeRFAAAEEWkyAALDFThjlIoAAAggggAACzSFAFQgggAACCCCAAAKtIkAA2CpnijoRQACBZhSgJgQQQAABBBBAAAEEEEAAgaYXIABs+lPU/AVSIQIIIIAAAggggAACCCCAAAIItL8AK2xdAQLA1j13VI4AAggggAACCCCAAAIILLYA8yGAAAIItKAAAWALnjRKRgABBBBAAAEEllaA2RFAAAEEEEAAAQRaSYAAsJXOFrUigAACzSRALQgggAACCCCAAAIIIIAAAi0hQADYEqepeYukMgQQQAABBBBAAAEEEEAAAQQQaH8BVtjaAgSArX3+qB4BBBBAAAEEEEAAAQQQWCwB5kEAAQQQaFEBAsAWPXGUjQACCCCAAAIILI0AsyKAAAIIIIAAAgi0mgABYKudMepFAAEEmkGAGhBAAAEEEEAAAQQQQAABBFpGgACwZU5V8xVKRQgggAACCCCAAAIIIIAAAggg0P4CrLD1BQgAW/8csgIEEEAAAQQQQAABBBBAYKEFGB8BBBBAoIUFCABb+ORROgIIIIAAAgggsLgCzIYAAggggAACCCDQigIEgK141qgZAQQQWEoB5kYAAQQQQAABBBBAAAEEEGgpAQLAljpdzVMslSCAAAIIIIAAAggggAACCCCAQPsLsML2ECAAbI/zyCoQQAABBBBAAAEEEEAAgYUSYFwEEEAAgRYXIABs8RNI+QgggAACCCCAwOIIMAsCCCCAAAIIIIBAqwoQALbqmaNuBBBAYCkEmBMBBBBAAAEEEEAAAQQQQKDlBAgAW+6ULX3BVIAAAggggAACCCCAAAIIIIAAAu0vwArbR4AAsH3OJStBAAEEEEAAAQQQQAABBBotwHgIIIAAAm0gQADYBieRJSCAAAIIIIAAAgsrwOgIIIAAAggggAACrSxAANjKZ4/aEUAAgcUUYC4EEEAAAQQQQAABBBBAAIGWFCAAbMnTtnRFMzMCCCCAAAIIIIAAAggggAACCLS/ACtsLwECwPY6n6wGAQQQQAABBBBAAAEEEGiUAOMggAACCLSJwP8HAAD//64iuEMAAAAGSURBVAMApCVBuog7ReEAAAAASUVORK5CYII="
           alt="ZEVION Labs"
           class="header-logo">
      <div class="header-status">
        <span class="dot"></span>
        <span id="header-platform-text">Connected</span>
      </div>
    </div>
      <div class="header-right">
        <button class="header-btn" type="button" onclick="triggerHistory()" title="History">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
            <path d="M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0 0 13 21a9 9 0 0 0 0-18zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z"/>
          </svg>
        </button>
        <button class="header-btn" type="button" data-quick-actions-trigger onclick="toggleQuickActions(this)" title="Quick Actions">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
            <path d="M4 6h16v2H4zm0 5h16v2H4zm0 5h16v2H4z"/>
          </svg>
        </button>
      </div>
    </div>
  </header>

  <div id="welcome-screen">
    <img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAABQAAAAHZCAYAAADZmpYDAAAQAElEQVR4AeydB5wcZfnH39ne93rL5e5SSCBUiYoUMZTQe7XwVwEV6QjSRXoREFAUEQERAemidAWMDQQNKOUgySW5Sy6XXHJ1e5//79nchs1l727L7O3s7HOfeW5mZ97yPN+3PzM7qxP8xwSYABNgAkyACTABJsAEmAATYAJMgAlonQDbxwSYQAUTYAdgBRc+m84EmAATYAJMgAkwASZQaQTYXibABJgAE2ACTKASCbADsBJLnW1mAkyACTCByibA1jMBJsAEmAATYAJMgAkwASZQUQTYAVhRxc3GMoFPCfARE2ACTIAJMAEmwASYABNgAkyACTABJqB9AmQhOwCJAgsTYAJMgAkwASbABJgAE2ACTIAJMAHtEmDLmAATqHAC7ACs8ArA5jMBJsAEmAATYAJMgAlUCgG2kwkwASbABJgAE6hUAuwArNSSZ7uZABNgAkygMgmw1UyACTABJsAEmAATYAJMgAlUHAF2AFZckbPBTEAIZsAEmAATYAJMgAkwASbABJgAE2ACTIAJaJ9AykJ2AKZI8J4JMAEmwASYABNgAkyACTABJsAEmID2CLBFTIAJMAHBDkCuBEyACTABJsAEmAATYAJMQPME2EAmwASYABNgAkygkgmwA7CSS59tZwJMgAkwgcoiwNYyASbABJgAE2ACTIAJMAEmUJEE2AFYkcXORlcyAbadCTABJsAEmAATYAJMgAkwASbABJgAE9A+gXQL2QGYToOPmQATYAJMgAkwASbABJgAE2ACTIAJaIcAW8IEmAATSBJgB2ASA/9jAkyACTABJsAEmAATYAJaJcB2MQEmwASYABNgApVOgB2AlV4D2H4mwASYABOoDAJsJRNgAkyACTABJsAEmAATYAIVS4AdgBVb9Gx4JRJgm5kAE2ACTIAJMAEmwASYABNgAkyACTAB7RMYbyE7AMcT4c9MgAkwASbABJgAE2ACTIAJMAEmwATKnwBbwASYABPYQoAdgFtQ8AETYAJMgAkwASbABJgAE9AaAbaHCTABJsAEmAATYAJCsAOQawETYAJMgAkwAa0TYPuYABNgAkyACTABJsAEmAATqGgC7ACs6OJn4yuJANvKBJgAE2ACTIAJMAEmwASYABNgAkyACWifQCYL2QGYiQqfYwJMgAkwASbABJgAE2ACTIAJMAEmUL4EWHMmwASYwFYE2AG4FQ7+wASYABNgAkyACTABJsAEtEKA7WACTIAJMAEmwASYwGYC7ADczIH/MwEmwASYABPQJgG2igkwASbABJgAE2ACTIAJMIGKJ8AOwIqvAgygEgiwjUyACTABJsAEmAATYAJMgAkwASbABJiA9glMZCE7ACciw+eZABNgAkyACTABJsAEmAATYAJMgAmUHwHWmAkwASawDQF2AG6DhE8wASbABJgAE2ACTIAJMIFyJ8D6MwEmwASYABNgAkzgUwLsAPyUBR8xASbABJgAE9AWAbaGCTABJsAEmAATYAJMgAkwASYAAuwABATemICWCbBtTIAJMAEmwASYABNgAkyACTABJsAEmID2CUxmITsAJ6PD15gAE2ACTIAJMAEmwASYABNgAkyACZQPAdaUCTABJpCRADsAM2Lhk0yACTABJsAEmAATYAJMoFwJsN5MgAkwASbABJgAE9iaADsAt+bBn5gAE2ACTIAJaIMAW8EEmAATYAJMgAkwASbABJgAExgjwA7AMRC8YwJaJMA2MQEmwASYABNgAkyACTABJsAEmAATYALaJzCVhewAnIoQX2cCTIAJMAEmwASYABNgAkyACTABJqB+AqwhE2ACTGBCAuwAnBANX2ACTIAJMAEmwASYABNgAuVGgPVlAkyACTABJsAEmMC2BNgBuC0TPsMEmAATYAJMoLwJsPZMgAkwASbABJgAE2ACTIAJMIE0AuwATIPBh0xASwTYFibABJgAE2ACTIAJMAEmwASYABNgAkxA+wSysZAdgNlQ4jBMgAkwASbABJgAE2ACTIAJMAEmwATUS4A1YwJMgAlMSoAdgJPi4YtMgAkwASbABJgAE2ACTKBcCLCeTIAJMAEmwASYABPITIAdgJm58FkmwASYABNgAuVJgLVmAkyACTABJsAEmAATYAJMgAmMI8AOwHFA+CMT0AIBtoEJMAEmwASYABNgAkyACTABJsAEmAAT0D6BbC1kB2C2pDgcE2ACTIAJMAEmwASYABNgAkyACTAB9RFgjZgAE2ACUxJgB+CUiDgAE2ACTIAJMAEmwASYABNQOwHWjwkwASbABJgAE2ACExNgB+DEbPgKE2ACTIAJMIHyIsDaMgEmwASYABNgAkyACTABJsAEMhBgB2AGKHyKCZQzAdadCTABJsAEmAATYAJMgAkwASbABJgAE9A+gVwsZAdgLrQ4LBNgAkyACTABJsAEmAATYAJMgAkwAfUQYE2YABNgAlkRYAdgVpg4EBNgAkyACTABJsAEmAATUCsB1osJMAEmwASYABNgApMTYAfg5Hz4KhNgAkyACTCB8iDAWjIBJsAEmAATYAJMgAkwASbABCYgwA7ACcDwaSZQjgRYZybABJgAE2ACTIAJMAEmwASYABNgAkxA+wRytZAdgLkS4/BMgAkwASbABJgAE2ACTIAJMAEmwARKT4A1YAJMgAlkTYAdgFmj4oBMgAkwASbABJgAE2ACTEBtBFgfJsAEmAATYAJMgAlMTYAdgFMz4hBMgAkwASbABNRNgLVjAkyACTABJsAEmAATYAJMgAlMQoAdgJPA4UtMoJwIsK5MgAkwASbABJgAE2ACTIAJMAEmwASYgPYJ5GMhOwDzocZxmAATYAJMgAkwASbABJgAE2ACTIAJlI4A58wEmAATyIkAOwBzwsWBmQATYAJMgAkwASbABJiAWgiwHkyACTABJsAEmAATyI4AOwCz48ShmAATYAJMgAmokwBrxQSYABNgAkyACTABJsAEmAATmIIAOwCnAMSXmUA5EGAdmQATYAJMgAkwASbABJgAE2ACTIAJMAHtE8jXQnYA5kuO4zEBJsAEmAATYAJMgAkwASbABJgAE5h+ApwjE2ACTCBnAuwAzBkZR2ACTIAJMAEmwASYABNgAqUmwPkzASbABJgAE2ACTCB7AuwAzJ4Vh2QCTIAJMAEmoC4CrA0TYAJMgAkwASbABJgAE2ACTCALAuwAzAISB2ECaibAujEBJsAEmAATYAJMgAkwASbABJgAE2AC2idQiIXsACyEHsdlAkyACTABJsAEmAATYAJMgAkwASYwfQQ4JybABJhAXgTYAZgXNo7EBJgAE2ACTIAJMAEmwARKRYDzZQJMgAkwASbABJhAbgTYAZgbLw7NBJgAE2ACTEAdBFgLJsAEmAATYAJMgAkwASbABJhAlgTYAZglKA7GBNRIgHViAkyACTABJsAEmAATYAJMgAkwASbABLRPoFAL2QFYKEGOzwSYABNgAkyACTABJsAEmAATYAJMoPgEOAcmwASYQN4E2AGYNzqOyASYABNgAkyACTABJsAEppsA58cEmAATYAJMgAkwgdwJsAMwd2YcgwkwASbABJhAaQlw7kyACTABJsAEmAATYAJMgAkwgRwIsAMwB1gclAmoiQDrwgSYABNgAkyACTABJsAEmAATYAJMgAlon4ASFrIDUAmKnAYTYAJMgAkwASbABJgAE2ACTIAJMIHiEeCUmQATYAIFEWAHYEH4ODITYAJMgAkwASbABJgAE5guApwPE2ACTIAJMAEmwATyI8AOwPy4cSwmwASYABNgAqUhwLkyASbABJgAE2ACTIAJMAEmwARyJMAOwByBcXAmoAYCrAMTYAJMgAkwASbABJgAE2ACTIAJMAEmoH0CSlnIDkClSHI6TIAJMAEmwASYABNgAkyACTABJsAElCfAKTIBJsAECibADsCCEXICTIAJMAEmwASYABNgAkyg2AQ4fSbABJgAE2ACTIAJ5E+AHYD5s+OYTIAJMAEmwASmlwDnxgSYABNgAkyACTABJsAEmAATyIMAOwDzgMZRmEApCXDeTIAJMAEmwASYABNgAkyACTABJsAEmID2CShpITsAlaTJaTEBJsAEmAATYAJMgAkwASbABJgAE1COAKfEBJgAE1CEADsAFcHIiTABJsAEmAATYAJMgAkwgWIR4HSZABNgAkyACTABJlAYAXYAFsaPYzMBJsAEmAATmB4CnAsTYAJMgAkwASbABJgAE2ACTCBPAuwAzBMcR2MCpSDAeTIBJsAEmAATYAJMgAkwASbABJgAE2AC2iegtIXsAFSaKKfHBJgAE2ACTIAJMAEmwASYABNgAkygcAKcAhNgAkxAMQLsAFQMJSfEBJgAE2ACTIAJMAEmwASUJsDpMQEmwASYABNgAkygcALsACycIafABJgAE2ACTKC4BDh1JsAEmAATYAJMgAkwASbABJhAAQTYAVgAPI7KBKaTAOfFBJgAE2ACTIAJMAEmwASYABNgAkyACWifQDEsZAdgMahymkyACTABJsAEmAATYAJMgAkwASbABPInwDGZABNgAooSYAegojg5MSbABJgAE2ACTIAJMAEmoBQBTocJMAEmwASYABNgAsoQYAegMhw5FSbABJgAE2ACxSHAqTIBJsAEmAATYAJMgAkwASbABAokwA7AAgFydCYwHQQ4DybABJgAE2ACTIAJMAEmwASYABNgAkxA+wSKZSE7AItFltNlAkyACTABJsAEmAATYAJMgAkwASaQOwGOwQSYABNQnAA7ABVHygkyASbABJgAE2ACTIAJMIFCCXB8JsAEmAATYAJMgAkoR4AdgMqx5JSYABNgAkyACShLgFNjAkyACTABJsAEmAATYAJMgAkoQIAdgApA5CSYQDEJcNpMgAkwASbABJgAE2ACTIAJMAEmwASYgPYJFNNCdgAWky6nzQSYABNgAkyACTABJsAEmAATYAJMIHsCHJIJMAEmUBQC7AAsClZOlAkwASbABJgAE2ACTIAJ5EtA1fEkaKdbtGiRYcGCBaaWlhbb/PnznR0dHVWtra018+bNq5szZ04DPjfheAaOZ+K4Y+7cuXNmz569HcnMmTPntLe3z8JxG45bZs2a1Yjr9RQf59yUHqWLeBbKA/npIbRuobxxyBsTYAJMgAkwASaQKwEaSHONw+GZABNgAkyACTCBYhPg9JkAE2AC00+AHGx6crzBIeeCA68Oe/jlWrfDud3a2tr2xodD4bz7cnd391mRSORKm812qyzLP8f+V263+9dGo/Fhq9X6qNPpfNxkMj2F88/i+Dmc+4PFYnmexOVy/cHhcPwe557GtSex/51er38YYR+UJOneaDT6U8S9IxaLXe8dHb14xowZ32lpaTm5qanpkObm5i/i+DOQ+ZA2OBgbyGkIvazARY5CsgGHvDEBJsAEmAATYALpBNgBmE6Dj5mAygiwOkyACTABJsAEmAATKAIBiZ7ggwPNRk4+ONHmwsH3BTjSjoXDIiV+0AAAEABJREFU71w43240GAz3QB7G8ZNVVVXPwkn3exw/DUfdI3DM/RJyCxyAl2L/HTjsvhIKhY4NBoOHQw4OBAL7+/3+L+L4CzheCNkFsiAcDs8jQdgFkF0gn8XnvbD/EtI4CGkdCWfiiZBTIKcjr/OETvcD2H8bPv8Snx+FA/FJu93+DByIz8CR+ATpiPP34vxN0P98OCePgzNwDzgrZ8O+OoiNbEUavO4BBN6YABNgAkxAvQSKrRkPhMUmzOkzASbABJgAE2ACTIAJMIHSEZAWLlxopK/VwkHWBufY5yHHrV69+ntw6v0YDrSH4FB7As60J41G4wNwwt0Ex965cMx9GY65g7H/Ahx5O+JcO5xwjQhTjXgOONyscAiaEd8IB5xBp9Ppdbqk6FJ/UpZ/qfBjeySnNyAfEpPZbLYgP6hod2JfjTCNiUSiIx6PL4DTcA/IYhyfCL3PhtyIyA/AOUhPFT5BTxRCzztWrlx5KWz+6syZM/fFfi44uMe+WsxrodLVS845MwE+ywSYABMoGgEe9IqGtqgJ09cbSKj8xgudT4kBWhho0peP0MSIZYGp1AwmKju6m50SlHOqzFP78fUi/TOCK7LRV2zS000dp3TYap/Sdfx+IvuUPl/qclQyf6XZ5Jve+LJMfUbtSi/7VL2gvZ7yonAIk3EjTnPnzjWnhUtPa8sxpZEuFD4fofyykanSTtcFhm3RM+2Y7M8kCMJbFgSov8nEdZtz6WWRfjxVGU7X9XSdYPcW/ek86UD1kfa4lu2mp7gIvCWt8cd0fSKhvLKVidJQ4vw4ndPbSsouBKmUrWA7ddSPQurh6Nq5vb39mJGRkUuR6r1wpv3O6XQ+DkfafXDmXQvH2elw8h3i9/s/A2nF5yo49KwQcuqRQ0833oeHdEq6jdcHDkHa9KQzHJIWfHDDKTgTzsCFsPFwyOk4dxVs/zm8iI+R/Qj7IJyaN7S1tX0VsnDO5ncWWmAY1TfseMuSAPGi9QbtU5Kp/aauJfurfPoM6EP5KCopPZD2Fv1yOE7ZSePTeEEyFbcRgxST1H78uXTOhhT/1D7bsSjfcKl80vcopXSdUnqn9qlrCMYbE9AGAarc2rCkQqzo6OiwYKLy/VmzZl2NO5gXtbS0nNPU1PTdhoaGMyDfra+v/25zc/M5M2bMuICu407nJQMDA0kZGhq6NE0uw/FlmBBeNjw8fHmaXIHjpGBCeHkR5DKkqYjgjvSlqbRSx7SfDsGk8VKSVF4pPTLsMzLEXfQrppDLcT0po6OjV0CuHJNk2VDZ9fT0XIa795esWrXq+1QnUNbfQ9mf39jYeA7qwZmoD2fU1dV9G/Kd2traM3H+PFz/GvZ2UeAf6p0NeX4NC4tLUBcvQr08H3IedDifBIuN7+H6RZDv4/zF+Hwp9CW5DPvL1q5de/maNWuuIEF9S9Y/qovpQjbS59T1yfapcLSfSFJlldpT+U0il6RdG39Mn0sqYJPelhU/TvUZU+1R/y5Nk2Rd7OrquhD9T7Iepvom2qMunIcF1hU+n++GDRs2XIK6485QDXVYoF2Dekpf87oDZXntdtttdznq2IWoR1R/z0Xa56H+fQ/5XNTd3X0x8r8EcinpSlxony50bpxchnSTQnWK6kOGdrulj0pdp7BpkmyHqc+UHur15ZBkm4SuF8Pe7yf74JYWapfnod1RX03t8rs4Pht20Hnqpz8vhKAJstjqjz+kE5BQJ/ahckf/Qk/x0Nj3Pfo8JhcSbwhxp/pAsqVuUrmQjKsHW9pNqr5MdB3nk+Nl+j5V9mn7ZJ2gftrr9f6AxOPxXIX6/sMxuZr2dA793pXQ51Lsqe+mfpL60EvQLn6AfudGtIG74Li4Yeedd65Oh5DpGA6eHdGWzu/t7b2K6h34UPs4G1zOBjNqL1THLqJxApJqL8SHJMkoZX+G/cU4t5VQGoVIWptN5U/7S+g8tRe0i/NJd/QZZ0HOhA3n4/zFJDg/PxMDPpckkHzCD/WBHH6fRV97CpxkP7JarY9WVVU95Xa778PnK+AUOwl92hcwv+hIJBLVcJYlHX1wjulxPbklU9PAv6Qxm//p9Ho9PUloxkcXGLTA/t3Rxo7B+QscDsfdLpfrd0aj8VF8vgvt6DvoS/bGvKUZPM1Awf0zIGTaampqXGizl2w3Z87N4HUFuF2Ctvp9nKPx7Xy03+S4jf25kPNx/gK04wsxfpNchP14ofMpoWup4+Se4mYQGgsuxHnaZxK6RpLpGs0lSC6E3jSuULiMQtchFI7mOBfAnnNpro059rewPxXyzQbMudFvnYUx/lTUo6ZMzLR6jm5coQyOwHztSqoLY2V9VnNDw3fA6HTIt7AuSc1/qJ+/CGFSYxKNRcmxAGPORGtWGrO3GYvHxmW6tpWk0hnbbxnHMF+kfCi/S2ncQR1Mlid0Pxv6pdZNpO+36Rzq9MUoyyux31GrZcd2VR4BdgCWWZnjLmYzJm0X2Gy2y3AH8zrIzZi4/AiTu1shP6qqqroF527EhOZa3OX8IcJdiQngFRaL5QosKkguT9tfjgnP5ZgAXpYml+I4Kbh2SRHkUqSpiODu7WWptFLHtJ8OAaMks1ReKT0y7LcwRJwkV9oj3iVTyKW4fmmaJMMj/ctQfpelyhNl+wOUMZXz1Sjv61AXrkc9uLG6uprqBNWH21AnboUDkI5vxPH3UX9aCq32yH8G8vgh8r8Wch3kRpy7CTrcCLkBOl2H/TUkuHY1Pl+F6z+AXEkCBqm6uKX+wbZkfUztYWfyM8ImWU+2pzh0nfZpQvXjMjDMKBR+EtmiF8KMP6bPmhAwvoLso326oMyo38hVfoB4V6HMr0b/cx3VQ/RFt2B/K+rlrbhGjo0rkd+FOD4PC61ZmeohFmgL4VD7GhwhZ2KBdjHKk+rNtag3NyAd6ttuQLrJ/g060zWqU8k+DmlfjnDJ49Sezo2TLfUBaVMbo3oyYZ9E9YfCpQRpUX1MtWU6pvjUr5JcAdtIp2SbBIdr7C4Xtcsb0PZuJhZoN7dBqH3eCHuux/nr4bRpzsSCz20mAAfXXGIGXteB6Q8h19JxmlyLPob6GeJ+FZUBZEv9RT2h/mYbQVkm2/EkdSV5HeGS5Zy+p/qQ/hnHyTqB+nIJnCkXo35Tn30xjr8/Jhfh3MW4fjH0oT78B4hD+l4D3a+BDlch3GWQ78Xj8W8j3OFwUsQ2E5j4P8IdjUX41UjzcthMDG4An5tIwIeOiRnlQXml2FBbJdnCCHGTx9Ajvf1Q29pKEI7iZSWw66rxgvS36IBrNH4lw9B56HwN2vb1kJug+y2QVDu5BvZdBn77T0yiIq9ItOieN2/eDLSRQ+Fcvgn18ilwfBI8fwIiZ/r9/v3hlJ6HfS0+W1G/6Gu6OvSzyQ3nKm5LGo5/YEFrICPaWRXGm7lwDB4AGKejjv8IDB9F+3wyFov9BAv/E+HQmAUnAD0dSHEQjDcigPrWJsvyeRabjRypPwC3q9F+qR3fgPGOxrh0uR7Xad1Cc0Oas/4Q84Utgv7gKgj1UykZ/5n6/kxCcw46T/tMQtd+OKYX6baVQIeUPindt7pO8dAXXQvdr4HQ/lqcux7nbkTfeyvkDoxPP4XcXV1beyc+/xjyE1Sxn6JdZrrRSeg0J7jpVQenKI1BV4HTD8GH+vFbXdXVxIMY/RjzHVqr0vhE/TzxTo0HNP7Q3InGm+QYhD4/fcxOzq9wjvZbBG00NTanxuote/SByXTG9pRuutBcja5vmbNC55tQZ3+EcqQ52o+xvx02/AhlfR3SuBR3SPbTXKGxQaokMB1K8UA2HZQVzAMTFlcikbCHw2EjJisWLBRs+OzAALxF8NmO8yQ27G2YwKT2VjqG0H5Kobgs8SQ7JThky32ycFTmKckUjvSk8h+TreoEhcfEthoDWaLQKon0sRbTV4VCISMmzxbotFV9os9jQtcmFdKrSLKl7hMXlm3rMnEnLrRXQiitMbGjjthT/RId03nUFTMWWoax87SYGl8VE1is3oWFbDwQCOjG6peZ6hL0U6wtki75CvTYqq5n83l8XhSHWNB5OHD2IhkPgj9/SgB8ToB8BvWA+hIz+FkgOZfD+DjEvxAZn1765zFdSd8tQmWefj49PB3TddR5A9qIjP0Ty5Yt835KIfMRFpkuxLUhvIHSJnuovZHgODkPwPWsWSGOYu1sqnxJ35SkhaX8k/0H2UDniQv2dhq/MlOoqLMSPZUGxx/98u3RYHMX2sYfsVB9CHt6Z98X4ezrQH1wo581Yc6IdStqCbaKopSjscCT2qgdOTHHbkP92xsOhtPhFPgF5k2/R4D74QT8vzlz5sxtbW2lXxuu+DUU+gszONnQZxnAzIT6SOM19c8kVlyn9pyVIJ0t7b4Yx7noMj4s+p9t+tBUmHRd6dwYA/iTHMehzlyJtmrKsTqWZXD0P0YY3Yi+h+oCrVFprKY6QOWaXI+kWBEnkvFc6VxKxl/L9DkVNp890qM1AumXrJ+kG/pM0tOJPYkDYayo18l1js5gyDRnLcuyYqWZQMUPXuVWBeB1oTLjryOUW8GVQF9MPLZsY9nLGCTDmNBOuagcCz/hDoNiDAPkhNf5AhPIgkAkUxhU2jew4Pog0zUtnsNinRwbJ9KiXov2FWoTFtxNcG7QL4vS1/c0P/ahX5XBbC0cY7/AfsoN4eMUCO1G02xgH41fUz4RSSzKX7a1gN5XBcfTTMjJWKjSj3S8BIfoA3DwfQsL1N3o6RvUGTM+V/TTfduSy+8M6ltyA2tyyNdgzrOL0Wj8ss1m+ylSfAHOjt/NnDnzu7Nnz55X4X03uiDqskCFt60IwBGmr6qqOgf7U3CB1m7YaX5L9j9atBLrp4odf7RYnpVuU6V0SJopZ0zwaFKiGXvYkOkjgFmaQP0JezyeQKG5YvIbQ0UUEE0vOgvlxPEzE0BdpKdQw5mudnV1hXEH/RbU1QjCaX5lgQWmwMJyH9i60xYefLCFAPqaxfiwA/hURF9Diww4G+5Zvnz5AOyecgMXzbcRggA7BdhE6biSpKWlxQYn0+fWrFlzK5x7r+AG3n1oEyeDxYJgMFiN/sOIcVizi261lDUYS2Ctx9jkgk7zUA5H2u3223D8As79qq2t7YD58+c78bki+inYmdxQJ+kGREX0QUmDc/hHfZbH47FUV1dfjza8MIeoZRsU9UGz9R/lWXHjT9lWRFZ8SgLsAJwSEQdgAtNPoFg5YmEZwaS14LtYer2eJ33FKqQKSReLqQknU6inL7jd7g8rAQUtLH0+XwMmzsfCXs1OnmFbzltHR4fF5XKdNDIyYss5chlGwAJDRrvoQ524Pwf1yZmeQ/CyDUpsaNwpWwNyUFw/a9asxjlz5nzZarU+jfrwPJx+56B+7ABnkwN1xIBzyS2HNDmoQgQIPMpAh3HKhuO5KKOvonyeDIVCr8ycOfNMeicjPbGpUHaqTgb2J1AvVa1jKZUDG8nr9Tabzea75s6dW19KXbWkiNsAABAASURBVIqdN+YwNBZp1hmMspxwzlpstpx+5RCYLkvZAThdpJXLhzpXXiQqx7OiUsKENVJbW1vwIgqLkILTqCjwbOx4AvR1vgknU/QUYDAYvD4ajWZ8SnB8Yhr4rLfZbCdg0d+mAVsUM8HhcHzBYrF8ARPvihjz4vF4DAvFe1atWjWaLUSwoUVXtsHLOlw8Hp+wzyhrw8aUX7hwoRF9wHw4Cn4Ix8obsPcB7A/G5QY4nIzY46NUEW0BtpbFJuEP5aSH86PGaDTuCbnD4/H8vaen59729vbPoCzNZWFInkpiTkk3lGldkmcK2o+Gtiuhn/4C6snNdFNrCovL9nI4HCZnsGbrAsqQ6nrZlg8rzgTSCbADMJ1GeRzz5K88ykmVWmIiEl66dGnBzjvc7Y5jMNTsQK/KwtOQUqiHAnVo0smUyWR62e12f1wJ9QxrSAmLxlnY069QaqikCzJFwmL6pE2bNlWDi+bHParnaBd92N+bCzWEL7g/zyW/UoYFn0n7jFLqVkje9LTYdtttt8PIyMjPg8HgX3Dj43LU+R30en3yhyZwrPn6Xwg/FcWV4Ag02+32DpTZqbiJ9RrK8kU4AvfU6g9BwM6KuQFRSD2Do1SHevF19NdnIx09RHMb6gLM0+6yQKvjj+YqIhuUFQF2AGaFST2B0LvyRFA9xVF2mmAAoycoCh6hMcmlNEjKjgErXHoCNFGc6inSzs7OiN/vvw11NuOPhZTeCmU1QJuiX9A7tbGx0a5syuWZ2uzZs7ez2WyHofwrYp4CO+Nms/lXXV1dnhxLrGL6YTDSmrNBD8ff7DVr1tyKvu4N2HcqnARNcHzTe/3QTUo838uxMaghuIQ/9Oc6p9NZjRtd+4dCoZfD4fAf4Aj8DDl71aCjUjpgHNdam1QKzTbpoB4Ya2pqruzo6Dhwm4saOIE6r+mxCM1akzegNFD12IQ8COjyiMNRSkgAE0R690sJNeCsi02gmOnDgUwOwIKzwEBIAz0vTgomWZkJoB6KbCaLmDD/we12dyE81TdNw0KbkgKBwG4Wi2UPTRuapXFwghwzNDTUQlyyjFK2wah+oz306fX6nJ7+I4MRt1KeAKSqoBVng27WrFntkKvR5v+Ked05Vqu1EeVP8zseV6lia0CowpI4HA764ZCD4Sz7c09Pz0Otra07a80RqIHimhYTPB5PFf7ugDN41rRkOI2ZoK5rep6GflrT9k1jVeGsJiAwnafZATidtBXICxNEenScJ4gKsKzAJLBWlGmxyINYBRa+ykyW4fCYcjHf19cX8Pl8dyKsIo5rlTHYRh00UDv+zsAF6uexq8ytra2tura29stYMBsqgQDKPY7F04OffPLJYK728qIkV2IlDa+bPXt2Gxx/l6PMl6DsLoPDf4bBYEg+8VdSzTjzohFA205u6NtrMZZ9BfJad3f33R0dHdsj03Jfh/F8EoWY7UYVAXOa7XGD60djvxqdHpWP1U2A67q6y4e1y4FAuQ88OZiqjaDxeJwnitooylJZwY+wl4o857sVASyCsppMmUymJ5xO52osmLMKv1UmZfaBFgdwChwAB8GOZaa6oupicXRQIBDYnngomrAKE6N6DXv70B5+rkL11KZS2fYBra2tNXPmzDkdZf1nQP0hbua243jcfA5XeNM0AfRpOpR7PfbfRh14bcaMGZc2NTXVa9poNm48AR3K/liMcZdo7UlQ1OvxtvJnJsAEVEiAHYAqLJTJVMLi0ITr/AQgIPCWOwEsNhVzAGKg53qYexFwjDECqD9ZLeY7Ozt9o6Oj9xgMBsXq7pgK6ttBo0gkUo0F4jdwWJHta+HChca6urpveL1eCxhUwob7evGHli9fPlAJxhZiI0AVEr0kcak+d3R07OF2ux/Eov/OWCy2HfYm9H8V2b5LUggqy5TKHnWAnvJusdls17hcrmfa29v3p7qiMlVZnSIRQF9mQLmf39XVdXSRsuBkmQATYAITEmAH4IRoVHvBgsmDapVjxQojUOzYcADSV4CLnQ2nzwSmJKDT6bJyAFJC0Wj0t5gsr0H9zToOxStHQf8uwQF4wg477NBUjvoXqvPw8PAeKOe9Ck2nHOLDThmO7fWo3zm/+y/NPs23iXK1debMmS0+n+9S9F1PhsPhI7Dot1H7TrOHDyuYANUF9AEm1It94Ah8YmBg4CY4izuAhJ3DgKD1LRQKOaqqqn48e/bsnbVuq0bs43apkYJUoxnTrRM7AKebeIH5YcJgRRLcCQECb3kRUMwBiLrI9TCvIuBIWPTIfr8/a8fFmjVrhkdGRu6DY0zzTwFSuwoEAi1wGFTkkwFwlnxlaGjISRwqoKXEY7HYwz09PesLsLUi+mHUhwIQTW/UuXPnmufMmXNIXV3do3q9/kr0dTOhgR42VERZwVbeciBA9SISidRifLvA4XD8oa2t7UQ4AivlCegcSGkrKJV7MBhsM5vN98yaNatRC9Zhbsd9nBYKkm3QPAF2AJZfEfMd5PIrM1VojIFZYMKhiAMQafEgr4pSLV8lUBezdgCSlYlE4iE4h9ah7uUUj+KWm+h0Or3dbj+npaXFVm66F6Lv9ttv32GxWI5BGWu+f4GNMhZ+6+Ho/UUhzHQ6neZZpfjk2mek4k3nftasWY1w5FyPvuo3Xq93X5SvGXpnUUbTqSXnpTYCY3XEAIfQzk6n8wF8/gnqUrva9GR9lCWAcpai0eheBoPhjtbWVnrAQ9kMODUmwASYQAYC7ADMAEXNp7BoqKgFoZrLohx1Q/1RxAFYjrazzuVNYOXKlRtHR0cfweJam3U4rXhoURAIBOZardaD0k5r/hDOrOOGhoboBfmad5hQXxyLxR7t7e3t03zBKmQgmKm6XsCBPa+6uvoBLObP83g89MMOOmrLCpnPyVQAAaovoVDIrtfrT8cNgsdnzpy5L8xWdb2HfrwVRkCHPuMkk8l0GZLhdTkg8MYEmEBxCXBHU1y+iqeOBZKdJgiKJ8wJlpzAdCiABZTmv0I5HRw5j8IJoC/L+Uk+OP/uq62t7Uc9zjlu4RpPewomh8NxMXKtiHGann5wuVzfisfjBtis6Y3qr91u74cD8EEYWmhdZucAIJZ4082aNWtfm832OBz3h0D4Rz5KXCDlnP3YHF8fjUb3qK+vf6K9vf10tf5ACHQttP8q56JSTPdIJKJH/3ERHL5HKpYoJ8QEmEBZECiFkhWxsCgF2CLmyV8BLiLcCkg6WgE2sokaJfDhhx+u9Xg8T8IRmNCoiVvMwsJK8vl8O3d0dOyy5aSGD8xm88EjIyOzyG4Nm5kyLREOh59ctWpVV+pEAXtegBcAr9Co9L4/tNFvV1VVPeb1eneDU1dfIXW4UHQcfwoCVI9GR0cbUbd+unHjxlvmz5/vnCLKtF/GzQy+AVE4dXo9jxQMBm24CXZPW1vbjgokyUkoTAA3rbmuK8yUkysdAXYAlo59Xjnr9Xp+AjAvchyJCGBCyYtFAsFStgSGh4fvwYJoExYelVCXHTab7fKyLazsFZdqa2sviEaj5uyjlG9IevovkUg8AAsqoQ7DTG1uCxYsqDGZTD+tqam5AzcmWtAn5blA1CYftqpwApiz0Y0gi9PpPD8ejz+93XbbzS48VeVSgH7chymEEyylUCjUjPHhsXnz5tUplCwnoxwB7t+VY8kplZgAOwBLXAC5Zo87EPQOQO6EcgXH4ZMEsOjkrwAnSfC/UhNAXcyrH+vt7e3yer0vGI1G7Sw8JigMWhAEg8EDOjo6miYIoonTs2fP3ml0dHR3slcTBk1iBOp9IhKJ/L6rq6tzkmB8SeUEdthhh2bckH0Kc7LT4fzjb2aovLzKWT3qF9Fn0JOlizHuvYzxYI9ytod1n5gAlXUsFtsZ/coj9HTxxCH5ChNgAkwgfwLsAMyfXUliYnAgB2BJ8uZMi0dgGlOOTGNenBUTKAoBLLh/4nK5BmT8FSUDFSWKhUC12Wy+UEUqKa6K0+m8EIseu+IJqyxBVFcZtg4EAoFfQzXNO7Bhoya3+fPnt8D592w0Gt0vFArpNWkkG6U6Aug/JDgCt7Pb7U/ROydVpyArpAgBKmcktBj767HP60Yp4vHGBJhAGRAolYrsACwV+TzzxWKQ7zTnyY6jCZFIJJR8ApAnJlyp8iKAGxkCk9u868/atWs/9Pl8r2ERrnknCljp4GT4SkdHhyUv2CqPtPPOO1eHw+EjyE6Vq1qweqjzMhbwL69Zs+a9ghPjBEpCYN68eTMwD/s9ynEPOK3z7sNKojxnqgUC5ARshROQnhBbpAWDKtyGjOajb9Hhxt/ZGPePzhhAvSc1PydTL3rWjAlkT4AdgNmzUkVILHj5CUBVlER5KoFFdrw8NWettUYAC5iCFs9wit3ldruHyamiNTbj7UG/34S2e8r481r4HI1Gz4QDsEoLtkxmA9VTh8MxDMc1vftP0R+xQd0oqC1NprdaroFfyW2cM2fOTPB4Nh6Pfw430xTSBynyxgRyIEBtAeNfKxxED7e3t++fQ1QOWkYEcJPBhjL++dy5c/lHQcqo3FhVJlAOBNgBWA6llKYjJvoV8ZL0NJP5UDkCdGdO0YWncqpxSpVGoNAF9KpVq/4Dx9FbcI6VN7ostEe/DzP1FyGopr5uiIWNWafTnQHRlF0op4wbHEdL1q5d+2bGi3xyUgJoAzR+TRqmmBfHnvx7Cs4Xdv4VEzSnnS0B+sEIehLw1x0dHfwkoNDmH+ZJ9K7RR9H/8I+CaLOI2SomUBIC7AAsCfb8M8UkmB2A+eNTZczpVAr1h58AnE7gnNdEBCQspAt9gkb2+/13uN1uz0SZaOU82q2EhUDHnDlzvqQVm8gO2HU8ypCebiy0LlByqhbUU18wGKR3/yndB2ueXakLln7wAzo8gfr6OQX6LSTFGxMonADqo4SbYDNtNtuD7e3texWeIqegNgLU3+AG2S7Q6+cLFiwwYc8bE2ACGiFQSjPYAVhK+nnkjQGfB4A8uHEUITCJoPeu8Y+AcGVQBQE4tKRCFVm5cuVfQ6HQf/R6fUmfDirUjmziw0az0Wi8PJuwZRJG53A4LjMYDMYy0TdvNTFuU/18B3X+9bwTmTgipT3xVb5SEIH58+c7UW73oAy/gD3PmQuiyZGVJoB6SU7ADrvdfjecgLOUTp/TKyqBrBKPRqOS2Ww+GjfLzkGEgudNSIM3JsAEKpwAT2bKrALgbpDmF0tlViRloy4mijIkWjYKs6JMYGoCCa/XexsWP76pg5Z3CPT99BTg52bPnr1deVuyWftZs2Z9dmBgYC76JM0vaJxOZ2BkZOSB7u7u0Gbr+X85EFi4cKERTr8rTSbTYfF4vAhfUy8HCqyj2glQHxqJRHazWCy3zZ0716V2fVm/3AkEg0FzVVXV5W1tbQfmHnt6YqAe8s2o6UHNuTCBggmwA7BghNOaAPpXiR2A04pcO5mNPQHIDkDtFGlZW0IOLSUM6OjoeD0cDv9XX45PAeYIADY6Id/TbHHgAAAQAElEQVTPMZoqg7vd7hvhWLGoUjkFlUK/i6ouv+twOP6oYLKcVPEJSHDafhXl9t1AIMDzruLz5hwKI4CuRncUHNZXTOdXRdG5af4GTmHFolxsn89X63K57sCcp0O5VBVPiZ2AiiPlBJmA8gTYAag806KmiMHWUNQMOPFpJTDdmcGDHJvuPDk/JpCJAPoyRRYOS5YsieHu+K1mszmQKR8tnYtEIpLVaj0GC7yacrZr1qxZ7R6PZ0/0R4rUATWzgAMpPDg4eP/777/vV7OerNvWBObMmbNndXX1dainrkqop1tbz5/KkUA0GjXa7fYz/X7/KdBf830rbKyojfohlPECeHpvp1cTVJTx6jCWnZvqKAdNaFFqI9gBWOoSyC1/HRbN/A7A3Jhx6DECmDTQV4DZATjGg3elJWDAn1IaYKH+KibG71EdVypNNaZDCwCMATWQs9WoX7Y6wSl2JcJaIZrexurjR9jz039lVNLz5s2bgT7l9tHR0VZqc2WkOqta4QQCgYDT7XZfPXv27L2nAwXaBztF8gOdV6x4PK6zWCxHoJwvXrRoET8QkhdFjsQEmAA7AMuoDixcuJDKi99DU0ZlpiZVaaKWSCT4K8BqKpQK1iUWiyk2eV26dCn8f9Hb9Xp9SOtIQ6GQ3maz/d/cuXPN5WjrggULHCin49Afaf4JFTg6oyMjIw92d3ePlGNZqUlnOL2npb5Qu4LD9nq/30+/+EtzriJh4GSZgPIEqF9F3Z2JMeK2WbNmNSqfA6dYagLhcNiEGxTnrly58sRS68L5MwEmUJ4EeHJTRuWGu9E6uvtTRiqzqioigEWNwOSQHYAqKpMKVkVSekEPh+Kr4PkB6nh5PJEAZfPZiBsc+TPh8Tw8n/iljgPdzwgGg1Uop2lx6JTKXvS3VA+XGwyGJ0ulA+ebOwHMsU6E8+SESCTCN1tzx8cxVECA+lbcKPosxorzoU5R+1nkUdT0oT9v4whQ+cLJ666pqblp5syZnxt3mT8yASbABKYkwA7AKRGpJwAGdHIA8mCrniIpSJMSRMZcTeavAJcAPGf5KQGqhPgkwTGi6AK7u7s7BMfSHUg/gvQ1vcFOs8PhOAtGltV4QF9ZcjqdZ8MJqPm5B5xIcZ/P99Dy5csHUE5F28CSHI1FS18tCWPRW3Q7sZhuqa2tvQQ3Wx3Ir6zallrKifVQBwGMg/Sk+OltbW27F1MjtJOit8ti6l+uaYO7BCdgO+YB9KMgTeVqB+vNBCqRgBps1vwkXA2QldLBYrEYcXda0UWzUrpxOuongAmDDGEHYJZFhQm0jMV1Am1uy4bPdBzDv1g0Go3hj/5tEZyLZJAwziUlEomEFZIQ0slVlMq7oHSIIXhEAkX4ZU04XV7U6/UfU9llWcxlGQz8hN1u/3x7e/tu5WQAnLSHo962onw07VxBPyvDwd2NuvhYOZVPheuqQ/9xHvql7VF+mq6fFV7OFWE+1eFwOFxvNBpvoK+1V4TR5WOkUppKmE99AWPNTR0dHRalEuV0mAAT0D4BdgCWURkHg0EjOnuemJZRmalJVUwIyQHIXwHOslDAi1g9g0n0DXC43AanxU8R9V4s6u/V6XQ/h/wE5+5CmyS5E/s76TMJHIR3jskdtMe15B7HtFdCUumn76dK98fI/8ew5XaS9GP6PInchmu3wdl5a0rgxPkR5BaSUCh0M+QmyI0kxAt9Fb1D61qv13uVz+e7AnK5x+O5Enesr0WY23H8M6S1FjwV3To7O31I9y4IlZ2iaaspMdRNCUztJpPpHDXpNYUuUn19/ZUof8Xe/ThFfiW7jJt1CdT1hz7++OP1JVOCM86JAJwkezgcjtPQf01D/cxJtbILjDFwy4axD0NNfMsNM4wlGDY+vRFGn6m/xj5GYbdExEHZGa4yhWmcwDxlEer0V4qlGoqJ1yTFgptFumhcBswDqHzPQ/CSlgXqGqoDPxCKcuCNCaieADsAVV9EnyqIBV9idHTUh8E8hMWFF589KcFnDwnuXnvHxId9vpJKY6s90vdOIsn8cX3CPXQdzUVSacGOdD222AQng58EC8pAmtA5H877KD7tMUDGeVQS9P4/cgDyE4Aiuz8sSEaxUrmyr6/v2t7e3iu6u7u/v2rVqvO7urouWLly5UWrV6++tKen5/I1a9aQXIE9yZXYX4nwPxiTq7C/au3atT9sbGz8YVNT09WTidvtvqYAuRZxJ5V169Zds379+mtJ0o/p8wSyJTw4XJeSDRs2XAe5nmTjxo03QG6E3AS5sb+//6ZNmzbdPDg4+KPh4eHbh4aG7oTcNTIycgedQz7E9IaBgQFvdiWRWyi09efMZvMK7NU7E83NpIyhMQ5IdXV1h8+ZM6chYwCVnezo6NjVYDDsgsW+yjRTVh0suinBtRh3HqIDFvUTQN202O32qzG/qkX5lXQRrX5an2pIfSwJ6nocY2UYczUf+vkBzLt68PkDtPW/wan3R4T5LZwD92F/N+THSOFm7G/EtR8h7p2Qn+Pcr3HuWcgSCMVdg/niAM3j0NeFECaG88kNYXnLkgAYm61W69X09fYso3CwMiOA9mHGzYvL29vbD1WB6pqed6mAL6vABBQhwA5ARTBOTyKYnHowmP8Qi+ozcHwK9idDToKciEnSlzFZ+prX6/06jk+FQ+x0TMK+DTkDE7HvpgsGizMhZ43J2din5Bwck5yL/TaCNM4dk3OwT5ez6TPyPDtNzsIxyZnYk3wXaVK+tCd9SC+S70BH0pPkWzgmId1PR/ikIP5pkNMh34Ik98jvO7h+Bj5/F5PMMyGkA6V9Go5PwfWTsf8KFpzXulyujdNTQtnnUoqQWNjImITHS5F3OeaJBUcEq40h6E4TGpIEjonfZEIO1oyydOnS6FTS2dkZKaZAf3oyLhfJaAvSmYgBMUoX4pYuqWsUH8kov8FJO4q2T09Dku7KZ6CSFNGe6R1Addh/SyUqTaaGVF1dfZXH4zFBX007WIxGYwKOkAeWL1++bjIgfE09BFAnT8QNny9BI54TA8JkG8bE5IY5VhhzzkHMw97HvPQpjJfXg+NpFovlCOwPRqDD0A8fj/no1zEPOxNOqO/hhtmluEH2Q8gNuCl2I26OXQf5QUNDwyVwwJ6DdnMq0j0BaR6O8jgYaR2J9nS6Xq+/TpKkx5HPf5DeeogXaUeRB40tk6lb8dfATULZtIHhjRUPQ6MAqIyxdnLbbLafzZo1a75GzVSDWZqeu6gBcCXooBYbebKjlpLITg96qfhTmPw8gonSHzFJegXyKuRPWFi9DOffC5Dn4Bx8Zmho6Ek4Bh+HPDYwMPBomjw2ODj4KOSRMfkt9il5GMfZSCp8ap9MC3k9miaP4Zjkd9iTkC4kT+DzE6QfJLmHvk+NydPYkzyDPdnwNMKk5CkcPzkmT8Ce30GSdmzYsOFhyEMbN2787aZNm57s7+//A/Yvmc3mf2Fg3Bl8arLDq+1QYKFtAxW2DosLgYkz95EKc52O5LBQfBYLR80/BYhxQIfF8/+1trZap4Nrvnnssssu9U6nc3/0xfkmUTbx0Gesw2L712WjcIUr2tLSYqupqbkEddNc4SgmNB9jIW0JMAqifq+FQ+8FyOVw6h2JSIfjRuy31q1bdwvkaTj03sZ+OfbYrRvEXMzX1dUVphtbCEs3ZUjoBhDdDKJ9jG6M0XXM3fyIN4SIvX19fcu6u7v/BWfhM9jfinnvd9GvHw2n4OHo289GWg/ACfgB+sBh7NkZCCATbZj70TzmqLlz586ZKAyfnzYCRckIZSzBad6O/a9Qzq6iZMKJMgEmoBkCNChoxpgKMYTueJLkay7FVYvka8NU8fRtbW17I9CTJpPpJAyKmn/qBLZOuel0ulS5TxmWAwj6yrQOCxzuI0X5/WHBOIKF6o+xMKQFZvkZkL3GEhblc9DPHZd9lJKEvAA3p2hRovU76PSjQfz0X0mqWH6ZwqF0CG44boeF8zTVzfz0LEUs8vqhD8UUKtqH42fgePs2HH/7w8H91VWrVv0E/ey/4LBbR4476Ed9Lc0xcKjoRmnGKY+enp71yPO/cAo+ghy+BzkEOp2AsrsX/eAyOCJ90DO54RpvaQRQZlUoyyvTTvGh9gjoUM57Ye7zs0WLFvG7TLVXvmwRE1CMAC9uFUPJCamBQFNTUz3u6N+AAfBxTOwXYW/G5JAn9igccCCnFk3S8Ym3qQiAl4SVBNedqUCp9HokEnkOjrEulCEtINWjpcKawHlhqK2t/TaSVeV4Tk8nut1uekWFKvUDN8U21LkNWID9SrEEs0tI0/U7OwT5haJFMurmpegjTPmloM1Y4CHD6wd/WugjzKGuQZ0+IJFIfB1Ov8e6urpWdnZ2+mA5PcGHXUk2GY7AEDkEIW/U1NRcBMfkftD71EAg8LrP5xuCvgl85rbxafHQzaIjMD+u+/QUH2mNAOq8Hm3hZDjJv18K25B/KbKdrjx5PTBdpDmfohPQ/IS86AQ5A7UQoKf+9oHT70Wz2Xwh9jOgmJ6cONiraiuVMpjE6/DHdwWzLACqO1j8cB+ZJS+1Bevt7R2KxWJ3YCGoaac3TbiNRuPn4Wj7rNrKgPSx2Wz0FHYb6UmftSqoZwn0sfcuW7asT6s2qsAuRRdg6CM+OzQ0tDP19SqwreQqoI3K6DMjfr//PRx/x2q17r927dpbVq9eTV/HDUFBVTrUli5dGoVDcMO6deueRhs8Cg6QA4PB4POwwwM7VKkzWE7rRnUc5Umvw7l0WjPmzKadAMYiI+auV82aNWvxtGfOGTIBJjAhATVd4MWtmkqDdcmLQFtbW/Xs2bOvwh3rZzD5+yycXEaa7OSVmEYj0SQYk4IgxKtRExU3i+uQ4kinPUH0B8/BOdYz7RlPY4ZUT/v7+y0ul+s705htVlktXLjQ6Ha7zxgYGND8XCMSiWyE8+SBrMBwIFUQwI3Ca+AssqhCmRIrgTlCwuPxrAqFQhei3zxozZo1v+3q6toEtUr5pB+yz22DUzcIR+B/q6urT8Jc8Aiv1/sW2mYI9lW8IxBjhQ591NfoV69zo8qhFSIwLcmgnCX0a1asiR6ZOXNmy7RkypkwASZQVgQ0Pykvq9JgZXMlIM2ZM2cnDHR/wN2uy202Wz0NfCS5JqTl8JjMJ+x2+/KRkZFv4W7+P7RsK9vGBNIJ0AIWfcNdWPzRi+fTL2nuuL6+/li1TfYHBwcXms3mXQFb0Se3kJ7atgTsfAD963q1Kcb6ZCaAucNMOIf2mt75QmZdSnxWRh/phfPv17hZst+GDRvugQNtsMQ6FZx9Z2dnpK+v7x9wBB4C+y4IBoNrUdZl5cwsGMK4BGC/hLlgAxyi5427xB81RoDKGmNSPeb/f5w7dy7/wJHGypfNYQKFEmAHYKEEOX5JCDQ2Ntrb29tPCwQCL0GBvTFx5R/6AIj0DRMAGU5RXyKReAicDoQz5A+4XvF3wcGAtwoigMXf0+gf1qjC5CIpgbYuYeFeVowJHwAAEABJREFUZTKZTi1SFvkkq3M4HGcPDAxY84lcTnFQxzaiDO6Fzty/AkKxNixmFUsaaf0QYlcswTJMCHU2AcfYR9Fo9MttbW1nwmG2FmZoqg4vW7bMC4fmfRaLZXE4HH4ZZR7GDSFN2Ygyy3rTbf47GxF4/QcIWt7QviXMfT4Ti8V+ATuLfhMO+VVsuwJf3phAWRHgAaCsiouVJQIdHR3bu1yuxzCo/QQOrlaaz9D5cpDp0hGDfhwT3U6fz/dNHJ8J51/vdOWtoXz4R0A0UJj0bigs/O6BKZp+FyD6Q2nGjBnfbG1tVYXDbc6cOTNqamoOIr3AXstbAguf+9DHrtOykRqzTY95w5Eot6IvitXKDfOCmNfrfQHOsMN7e3tfovfoqVVXBfSSV61atRw3SL6KOdHtBoPBgzQr0llBdR5zwwbcQP8CGCixVWwbUgJesdNIJBKS1Wo9BQ7+bxU7L06fCTCBiQmo7Qo7ANVWIqzPhARoYTtr1qz/w8T15Xg8fjju6NpoMjNhhAq8gMm8bLfbvXB43I/jgzDpfYa+ClOBKJQwmRb2FblIUAKemtJAn/E4+gvNPwU4Ojo6y+l0HqwC9tQ1nzoyMqL5X5yEI4nek0bv/uO+QgUVLxsVcBNx50AgUEWVNJvwWgtjNpvDfr//ATjEvtnX16fpfjG97OCk9zQ1NV2Lsr8Atm/EtYpssxgPTXAC0i/HAwFv00SgJNlQHxeJRAxut/v25ubm3UuiBGfKBJiA6giwA1B1RcIKZSIwd+7cOQ6H415MWn6OO1rtWHTpaWDLFLZSz+GudhwTu/fhBPg6nB3nLSvyr1GCv6ZRo57JqGcVuUDQWsEuX768LxQK/Qp1VtNPAfp8Ph0cgOfQj2+Usgx32203NxYbX4aTQdNPh+AmC7qJxK9xo4W+OllK5Jx3DgRsNttFGCtNOURRIKg6koDzLwQH2N1VVVUXrVmzZlgdWk2fFvSk49q1ax9G33QW5kn9yLnixnj0WxLmNgfAdk33z7CPNxDAvEfC/MdZXV39uxkzZtTiFG9MgAlUOAF2AFZ4BVC7+QsWLDDNmTPnZLvd/kI8Hv8KJi4OGszUrvd06gcmMpyjo7jL90scH9bd3f3cND31p/nJI+paxS0OprPuTmNe9KL7x6xWa+m+Cj8NxqK+UpvcZ2BgYKdpyG7CLGKx2JFYcMwd02fCcOV+Ac6UTbgpdR/s4H4CEMplg/PjwHLRVUk94fCKBIPBB8Lh8NXvv/++X8m0yyytRE9Pz+9HRkYugjN4CPOmimq/1C+jDdQ1NTXtUGi5mUwmGnMKTYbjF5kA6ji90mYu6vuv5/KPguRFmxjmFZEjMQEVEmAHoAoLhVXaTKC1tXUGFpI/hvPvlz6fbx46XyNNXDZfLb//xdDYaDTG4fz7cGho6FsWi+V7xX7qrxg2qDVN1DURjUYNatWP9cqNwOrVq9cEAoHfYOGj6V+CHBwcNKHPPAN0SjK+d3R0WLDI+Mbw8LDW244MR8rD6HO7wZq3MiHQ1tY22+v11qB/ryjHBc0VMJ/6I4rpyr6+vgD2lb7Jvb29j3s8nusxh6o4Hqj/Zty84PfCVVArwHxWh7H5sHg8/kOYXZL5AfLljQlUHAE1GswdgBpLpcJ1WrRokQGLyGNqa2v/gMX6GX6/34XJCtfVtHoBZ6jscrnoDv5vcBf76DVr1jw9TU/9JbVIJBKaXzyhzhmwaHIkDc7uHzFJF6qz6aJHMuOFnCSTCrWH6ZIM+pG+6Tak24fgZbXJqLePYLG3oay0zlFZ1FvJ6XSePGfOnLocoyoVfDf023siMaor2Glzw0JqCA7AX8G6inp6CPaW9YYbZRcZDAZjWRuRu/Iy7P4ADsDLVq1aNZp7dM3GSKAd/wI3hh41m80xzVqZwTA4gSS3231khkt8SnkCqkkR6yl9TU3NhTNnzjxJNUqxIkyACUw7AVrYTXumnCETmIgAFq0NGzdu/HF9ff2vMVB9BnesDLSgnSh8JZ7HXdtEXV1dl8/nOwt8zurp6VldiRyKbTMWBG44Un48f/78n0Dumjdv3h0kc+fO/THkdtTVW2fPnv0jOKtvHpOb2trabmxvb78Bcj3kuizkWsS5Lk2ux/FW0t3dfUOa3IjjLQLH741TCerHTZnCIJ1Uutfj+HosDCnfG9Lyp+OkYLJ43YwZM65paWn5YXNz85Von5egDl6EieT3qqqqzoUz+uwxOQ/nLsL1H0C+UewyyjV92LgSi70n4KDS9FOAw8PDLizwvporHwXC67Gg/vrQ0JAqfolYAXsyJkE3YCKRyOO9vb0rMwbgk0UhgDpdcLro148uOJGcEyhtBPTNm7xe72VdXV1cX8cVBd04xY2hqzCvehfjQiU58yXY3LT99tvzO+HG1QmtfxwdHTU3NTX9ZNasWbto3Va2jwkwgcwE2AGYmQufnX4COjhVDoLz4EWDwXAmFpBuqKBj5x8ojG206HQ4HEEsYJ7AAv+o5cuX/7arqys8dpl3ChPAYtMAJ/QhcLKeAzk3FoudT4LFwgWQ76E8LkKWF6GOfj8lmFBfjEXEJZBLce6ScXIxPm8RxP0+Cc5RGim5EJ+3EuRDeaXkAnzeItDxe5CkPqRTJqHwmc4j7wvHJGkHdL5oTL6PPcmWz7DrEqPReJnJZLoS9e+HdrudvjZ1Mxykt7jd7tvhBLyDpLq6+nY6h+sU5nSkT083YqeaLYEyfAA6D06rRtOcGcpbV1tbe25jY6N9OrNubW1tAttjkL+mn/6DQ2UkGAz+HGzV4EjWNGswTt8KddDosfitQR9bMczQX8fg/Ps15gp/TgfJx58SWLly5UYwuspqtVbUj6JgbmAOhUIV+T7MT0u/Io8kj8dTjznbr3FTu1TfFKhI8Gw0E1ALAXYAqqUkKliP+fPnO3fccceb6+vrH8fkfCGcLpp76q/Q4oUDRoZztAeLzrMDgcCpcP59gjQLXQwhifw2TBw1v4CiRSIcYVNtKBq9Af+SgsD6lKTOTbSHo9uogCAJg3GiPIp9HpknbYBzMLnhM31tOskCzmpyPpWsjk5Us7EQ/gTt6HmUk+p0m0jnfM6jn+jA4n//fOLmGwf5HT8yMtKYb/xyiIe+T0b9eaqnp2d5OejLOn5KAA7q2ejXK+brv1RX0SZX4KbHL0BBDc5qqKHObfXq1a/BKfICbnJVDCfcPKQhuyAHINqTpsdRddbWwrVC2Uu4sf0ZpHTnXP5REGDgjQkUh4BaU2UHoFpLpjL0kjDw7G6xWF6E9+DCTZs2VdOTI5hQaN65lG3x0gQefMKYlD6Dyekhy5Ytozv5/NRftgCnORzV3UqXFHJMMGkhpcbFQRzOsV9UV1dr+l1YmNzrcIf/cvSx5lSZFHM/e/ZsN/I7nfItZj6lThs2esPhMD39Fy+1LpWWP/rWgvoTOP0PwU2Ripn3Op3O6PDw8ANwbvVUWl3Jw15MPxO3oH5soHlXHvHLLgocw8JqtS4sRHH0hYVEV3tcGX1OosD6oFobQ6GQZLPZvoJ6cBaUrJh+EbbyxgQqngA3+IqvAqUBsMsuu9i33377q9xu9ysYhPbx+Xxq+6pgacCM5UoTDpp4YHBeBz5nB4PBU+D8WzZ2ueQ76MdO2pKXgroVQB0paLFeTOvg/PtfJBJ5A21MtToqYT+csJ+DQ262EmlNlQby2guO1R2mClfO16lOg+dLWDTTE9jlbEpF6o75xsFo89M8dpUGNdVVg8GwCvX18dJoUH659vT00NPhf8ENV02PC6mSgcdToI60pD7zfmsCqAcjAb//Xp1O2kjtaeur2viEtZceN7WubW5u3rtQi3CDpSLaTaGcOD4TUAMBdgCqoRQqSwcJjr95WCzSVy2uHBkZqcNxRUzIsy1mmmhgII1gYvYy7q4u7urqegDCT/1lC5DDqYWAWp8AFEuXLqUnY+6uq6vzFx1WCTNA/2HATYSroIIeUrSttbXVCufKeXCqavpGDhZKfvzdTT8cUDSYnHCxCEhY0O9arMTVli7avez1el/t7e1dpzbdVKyPjD7sXr1e71GxjoqpRs5wnU5nm66nxBVTfJoSisdjnmgs9mOvd/Q8OEsD05TttGeDMc2Jm6K/bmlpaVMgc806AdFeNGubAuXOSZQZAXYAllmBlbO6HR0dljlz5nwHnehfcVd630AgYMKx5p1/uZQZnH/0dYNN8Xj8gmAweMLYu/5ySYLDMgFVEKC6rApFJlbiX3CQvYnLWp/UHdPe3t4AO4u2GY3GeejTv6jl/hz1WUa//GfYuLRoIDnhYhKQsNCtRvlVxJzDbrePoE0+WkygWkwbTuJ/wwm4AvVE6+NCsvjg2KIfApmX/MD/tiKgl3R63IiPbtiw6Rlw+jWEbmpuFUYLHzC2iVgsNht9xgP0Kg8t2FQMG8CpIsaOYrCr1DTVbDc7ANVcOhrSbd68eTMwkD6MheKdmFw1YiDlupdWvhhYZAzAUez/gTuy+61atere7u7uUFoQPmQCZUUA9TkGhVW7iKL2NTw8/POGhgZNP12L/taCfvcclEVRJq+LFi0yWK3W8+BssCEPzW5VVVWh0dHRe/lp7PIs4sbGRis0N0E0v2EeIcPZuRzzrA80b6zCBlL7Rl/2CvrMinjHJ2zV48bG/gpj1ERyOixasDlhTBz7680m0zvUtvA5261swqGvgM9bOgD7axYuXJj3DyVplU/ZFCQrygSyJMBOmCxBcbD8CCxYsMDU3t5+GCYYbyCF4+EUsNIog2PexgjQgBkMBofg+LsUi/VDMAHtHLvEOyZQtgSoXqtdeTiu3sAC6L/QU7WOSuhW0Eb9LfqWb8+dO5cWMgWllSlyT0/PDJw/BlIUByPSLflGdRkM3wwEAv8suTKVrUDe7dRkMs1CWyjqV+G3LZrSnLHZbAnMKZbQTY7SaFDeuaKt/wGOEE2/HiJVQpiT6ywWy+dSn3n/KQH0+0Kv1ydfa7Fy5cqNXp/vArPZPIDzefdDn6auviPUeXSR0lnr168/DNppdjyHbbwxgYonwA7Aiq8CxQMwZ86chlAodAcm3r9DLttBuL4BQmobm0TEMOK+gwn74lWrVt3Z29sbTF3nPRMoZwKo36r/ukxnZ6dvcHCQ3gUYLQprlSSKRV4t+uIDi6COZDAYTkHaVUVIWzVJVlVVRQcGBu7t7++vCKeAasBvq0jei1IsbtvRJ22bogbPGI1GH5xYL2rQtGkxCX3actyMHZqWzEqcCW7OC8w/lXj3W4ktUT57dDYS+Gx5EhTz83e8Xu/VWNNElM9NHSnC4WmCPNDY2Lhjrhr5/X5NOkZz5cDhmUA5EGCHTDmUUpnpSI+Pw/m3NwbJ5+HcOgMTbyf2GEvLzBAF1J0oCUzOZQyyw7h+K/b0Qx/v4Zg3JsAEpplAfX39C3AMdKKP0uzkFf0NfZvpKnoPq5J4582bV2u1Wk+j9JVMV2Vpyagb72A8e1VlelWiOnm3UZRhM9p5pXrD/lgAABAASURBVDAbxbxiZaUYq7Sdy5Yt88MJuBZ1Ju/6prROxUoP83MBh3F1sdJXQ7r02K8eKxBsOalDhY+xbYsDEJFls9n8YCQQeBjtK/08Lmlng421GO8eb29vb9aOVWwJE5heAmrPjR2Aai+hMtNv7ty59bhD9gMMHr8Ph8OfxQTKAMl13C0zq7NXFwsQGWyimFT8BxOv47u6uq7CZNObfQockgmUBwHU8bJo92+//bZndHT0J9XV1Zqd0FONQT+8UywW24WOlZJoNLqfx+OZqVR6akynqqoqNjQ0dC/302osnex1woK9GW0g+whlHDIejw/D1sEyNqHUqmN6lvgQYxj5gEqtS9HzhwOQ3o9Z9HxKkYFDlxC7WMNiD8hsU1Q0G+OizpAQbn1CWCVZTLYIpgmMJEW2qgOYs4f15ugVJqPx31PUj1KYq1ieWKcsQGL3NTY22rHPeqM1TtaBOSATYAIlIzBZ31cypTjjsiSgnzNnzkKLxfIIBgB6l10dBkeuX2NFCSb0xJ9st9uHsAi/G7PLwzCRWILLCUjZbdCf5kZlpzcrzAQyEYhEIr/Honk5+qytJvuZwpbrOdimR/98Az2hrYQNu+yyi93hcFwEx0ryHUlKpKm2NOBEkcHso/r6+ufVphvrkxsBODmaMA5P47iVm35KhaY6C8f8xs7OTk2/1kApXhOlEwgE/ob6Qj9kNVEQzZxHH67JH8cxwcG3hy0iLm/wittaRsT9M4fE3S3D4oeNo+LMWq84yhUQu1vDYrYpJpoMceGCszB90aLDnz5u2GZ8W768b8Dn95/vdDg2UHvTTEVIMwR2SagXh6Lf/D790FfapUkPsTbQ7BwKhmt+/ICNvFUIgfS+rkJMZjOVJtDW1lYN599ZVqv1KUyaDsSkyUyDh9L5lGt64CGDTQx31JZ6PJ5TWlpaLl2+fPlAudrDejMBrRFYtWrV6NDQ0F1waCnnkFcZJOqTcfNhX9g5WwnV0Nfv4vV6d6F0lUhPjWm43e744ODgr+gpUTXqxzplT8BgMDRgLM4+QpmGhJ0C7XwD1NfyQhzmFXfDDaFucKwIJyr68G2cXNnSNWcbcJrD0eJ2Lhx7R8DJN8O42Y9rgvuGngBcaI2II10hcV69T9wJh+DNzSPi/DqPOLnKL3ayRIQBjkNSF22JvH8WOh4vq1ev/vfI0MDluKkfGn9NK5/J/wm5pKur62jYBHr4P8mGekR9jmbnUJOYzpeYQNkRoD6y7JRmhVVDQDdr1qxdq6qq7sNAeUswGOzAYMF1aqx4sNiQwUPGInIEk8n7IpHI0d3d3a8sWbJk82xkLBzvmIAWCaD+TzlhVJPd0PcpTGBXoc2qSS1FdcEdfRPsuwiJFtRPL1iwwGQ0Gi9FehkXR0i/7DfUBXpdQ5ff7/+dmo1BeZZVOysRSwn1tQ5tvETZT1+2qA9yOBzeNH05ajMntH8fLNP0ayFgX3JLJBL0mrzkcc7/zOp0Adbo4+IgZ1AstJIPd+IuUidJotUYF/s6IuKbNQFxab1H7GkLCwucgDTI1Vpk2wRM5Jkdsx9NeEd/ZbGY4hOEKfvTJpPJhnH+Fy0tLXtNZQz1PahL7ACcChRf1zyBcjCwoEVAORjIOhaHwPz5852zZ8/+hsvletzn8x2LO6VWCX/Fya38UsVCg14WHLNare+OjIx8C2guXLZsWV/5WcIaM4HKIADn/MjQ0NBP0GY1O5lHP0SOkOPnzp3bUkipBgKB2XAyfInSKyQdNcfF2Jbo7+9/cM2aNSNq1pN1y44AFqea/qXqFAXYKXCzkd//lwKS5x4O4xDmcZodC8ZhobXgxF6ycYHV/tEM591u1og40BkW9OMfuejbbo6Lc+p84ov2kLAadIbFjtCE78Cjm/lOg+kHNp30lsFgoKffUllpag/b6hwOx/1Y8203mWHoe2Q4ACulzUyGgq8xAdUToE5f9UqygqoiIM2aNWs+Jkc/s1gsP4Hzbz46fb2WF4K50gcLGYPlKAbCB7FQPmnVqlXPdnZ2RnJNh8MzASYwvQTi8fhj6Nt60adNb8bTmBsWtVW4YXMassx3waczm81nIr4LosmNyt9ut69HXXgQBqp6YYdxRtX6gZ8qNozL0zjfLZ3JsBNNXI6VTgNt5KzX6+MAqQ1jprACdubdh+BG0BSpT//lan1CLHaGBO1zzZ0GxRZjXHy5KiBmGmPSHKs86fsR/7lsmTcW9H3HabP2SRI8j7lmWAbhYRdhmY8138+333772olUxrjJDsCJ4PB5JqAyAhUxIVIZ87JVh7721d7efozb7X48GAx+FQO/Y2xgKFublFScJlEYAONOp/MDOP7OwiL7/BUrVqxSMg+1pAVbaUKgFnVYDxUSKMe+gZ728ng8P8Ud78K+xqLC8kipBIeRDs6tr2MiX5M6l8t+/vz5TXCUHo++TrPzB9zASWzcuPHhTz75ZCgXNqUIi3KopL44b1tR7/2lKJ/pzhN2UterWef8dPHE/M2IvPKub4hbThs9tZW3E1BNhhrhg9vBEhW7WvK/504DW6MxIXYzhxOzTbEp68C7K3o+9gwNnw0HWQBzY01wHF+m1K/Atv2j0ehVHR0dGV/9gbGIHICanTuNZ8KfmUA5E6B+rpz1Z92nicCcOXNmhkKhW7Bw/JXX690VM0wDZMqBcZrUK3k2GBjpK78+k8n06NDQ0Mlw/P2uq6srXHLFWAEmwARyISDD+feQ1WrtR/+WS7yyCYu+SmCi3oEbOIflozQWACfCAdiYT9xyiAM2wmazbYID4C7oq/rFHBZmqtcRHEu9ySjPAa226XS4qA8S5iEN6ef4OC8C9FqbilgjoV2QAzAvSGqKRIXVboyJ490B4dQX1i3GEN0j64RBFlmtc5Z1d/8xEgrcIUlCEyxFhj+M/XqMjd/GpVMhhBu7rTfML9gBuDUS/lRhBMrF3IwNuFyUZz2LT2DhwoXG9vb2Q3Fn63G9Xn82Fo01Ev6Kn3N55IDBTsZimJ76+wQan4/F41nd3d10jI/a3VAfspoUaZcAWzYVAbSFsqwjnZ2dw6Ojoz9B29bsRBb9uA5OzjN32WWXCd9vlKl829raquEg/Q7KVp/puhbOgUtiZGTkQdzAGSgHe1AWZdnOppttJBLRrFM/nSXmIwLjc1P6OT7OnQDqSwPaVt6/jpt7jqWLgSm9JpxWLl1cnFIdELtYJv/hj6lIR2RJvO03ifdCZmkgrst2rJP1RvMNdrPpFS3PHUKhkLW6uvqHs2bNOmA8R71eD9Nlzc6bYC+PtYDAmzYIsANQG+VYFCvQwTcODg5e63K5HsSC8Qvo2U0S/oqSWRkmCh4y1PY6HI6HA4HAcXAcPPT+++9XxNeMYDdvTGBSAmgf5TpZkrH4uw+LaNV//XPSApjkIj0lBEfXZ3w+3xcmCbbNJZPJtBhx52AYKNey3cam9BOwS4DLMPa34Tz179ipe0N5lIWepaYIx9jG6dGhtLmgPkhw0tMTutk6LkqrsEpzNxqNe4DjpO9/U6nqOauFOlP2DkCDkMXJVQHxRXs45x/+SAdGnWl3WCceH7GJ0YRe6ovrsx7rsAaIyKHI6XarZRnmP5RUetKaOIZdEtY7jW63+7Y5c+bMTTeKHIDoZ8u+LqXbxMdMQKsE2AGo1ZItzC49nH/7YiH0hM1muzAYDNJkkutKGlMa5DBB/BCD4XfhJD1n+fLl9NSfJgf8NLP5kAlkTQCTwbLtM+hdgLjTfQfad+5tOmtCpQ2ISbwZf2fRU97ZaNLS0mJDn3cuwmp2UQwessfjefCDDz4YgZ28aYhANBrdAHM0255hW3JDnyXQTmt22203Z/IE/8uHgIT57+fBsiKcqJjPFvK6GlW0qYOcIXG0OyQsusLUCcB99fCwXXRHjSKOpN70WXOqP++vXLkx6PN/1aDXD6L+IIWcopdFYNQXCTdJdzIa9dfPnTt3y/tGsRYik0GwLMxgJZlARRMo2wVaRZdaEY3v6OiogvPve3a7/RF08l9EJ29GdlnfAUNYTW80uuFuqQ8Lxd+CzfGrVq16vK+vL5BuNB8zASYghCRJ5Ty+0Mus79HyU4BwiEgOh2NfTNrniCz+sCD+DMp0VwTV5HhAfTv6da/P57sZNmpy4Qa7KnnrRf2tCPsNBkM1bty2VYSxRTBywYIFRszzZqFP0GRfl46M2gTmst70c+V2vKM5Ir5V4xVufWHfPqVO/yWvVbwZsAg6TqACDCf0Oc9jVnR3/zcWCZ6biMdDSIKSKjekU+obiUT0BoPp2FgsdsGiRYuSX5XHHCGOdhPVqs1TQuEAFU+gnADk3LGVk3Gsa04EdHPmzFloMpkewSLo2lAo1AoHINePNIQY2OJ6vX45Jkzn6HS6c+H8W4HLmhzcYRdvTKAgAmgnZf30BNq3B4voH2t5Mot+vgZ92uko6KkWupjsG85FH+hAWE1ucJoIOP8eXLNmzbAmDdSAUWiLU9XTCa1E3NUTXtTYBczdXGirOX29X2MICjInEAi0oF9sQJ0pKJ1yiAw7RTgU6i8HXTPpWA2n30X1XtFoLHwqvjaiF/T0X1je3M1Qil5Zl8s6aIuKK7vXPmkz6e5FO9TsE3GYP5icTuf5a9euPZIM7+jogD8wVsjTpJQMCxNgAtNAIK+ObRr04iymkQDudjpmz579HTj/nka2h6AHt2G/eQTEQaVvmATKmFAH4PR7Ane9jlm5cuXDnZ2dvkrnwvYzgckIYGFB40s59yM0/78HN0Q8k9lZztcwgddVVVUd097e3jSZHfPnz29Dee6LsaGcy3NCE6mPhwMwGI1Gr5kwEF8oawJGo7EbNyWoTRfRDnUkDUe2AfOVg6AN9cHY8ZYLAfQH+6M/cOcSp1zDwk7ZHwi8X47664Qszqn1iLnmWMHqJ2RZ3DXgFIPxre9bxhPIJL/UE3qL/yqH2fQ31Cet9jsSbpJWY4503fbw/i1ZsiSBNVIwP1wciwkwgekkwJOD6aStvrykuXPnzkGHfT8GqNux+GnH3aqtRz/16TytGoEH5gVyNzK9AA7SM8Z+4VfGZ96YABOYhAAWoGU/vtBTgOgXf4r+Mbs2PwkPNV6CXaRWB8rqeDqYSDBGnIZrDRCtbvSV79+gvEe1amCl24Wx24v6rtmncdLLF/MWYbFYdp45c+akjv30OHy8hYAezuITcNOXXn+z5aRWD2ArfW3z74XYh3ZVkvHxOHdA7OuIFPSjHym7X/RYxTuBbYs8TI0pFSjH/fvv9/vlaOybJqNhTakY5ahyPsGlcDi8Q1CWb6D+plJusuQDiuMwATURKPsFmppglpMu9OL32bNnHx6LxZ7HROcELABt0F+TT3fArny3KAazVzFwH7t69eoHsnnqL9+MOB4T0CABGl/KvU+R4QC8A4tpzb7nc2RkRO9yub4yf/78jD8a0NbWVo1F4nGY5FN5arCaJk0KB4PBK5JH/E+rBBKoxz6M57JWDUzZhXkL9bttsHXesLQwAAAQAElEQVT/1DneZ0cA8+LZRoNhF/h9iGF2kco4FOb+NM/9T74moK6VpD3tYI6Ir1X5hK3AH/0guzdFdeKXQ47ko37phS7DsrAkFXTT4MOVK9cmoqHTZDnhkfFH+WlNUAf0EFpHPmG32xdqzb6UPWgr6dUjdZr3TCBJoNz+aXlCX25lMW36zps3r25wcPC6UCj0G71evz2EOm/u2MZKAAOZjAX/AD7eiMX/1+D8+x+OC3u7MBLgjQlUEgFMlsjcsu9Xuru7R3Gj5BcwBssB/NfYhv6OfvHyMyivfTOZhvHhCPSHsyhcpuvlfg4L/YTJZHoI5cy//FvuhTm5/jIcgPTe3slDaeSqz+czW63WkxYsWKDZX+0uRlHJcvwbOr1ey087j8cW7OvrGxp/Us2fq/RxcXatV9QaCh+S6Zd+fzHoEJ5xX/0l+5G67I/J2X6/mKJklGUre/6iS8j041LRjAE0cFKv15sMBsPeWFc2a3WuoIFiYhOYwBYC7ADcgqIiDvQdHR1fQAf9e9yIuhCTw2ruqD8td7AQWAjGzWbzv4PB4Mmtra038gvhP+XDR8oQQNsr2y0XAjBSWrhwoRbGGNnv99/scDiCsAlrglwolEdY3BCyoO/7OsrLmK4x+kArzp+K/tCSfl5Lx1i4BMLh8OVasoltyUxgdHT0tcxXlDirrjQwn5FsNtueXq93D3Vppl5t5syZ02Aymo/CHNmgXi2V04zGs3g8PooUy+YGt0mSxdeqA2KeJS70UuH3F/8XNIq3gmaR6TE/4iPLCSWcdnIgErnbZjW/hPGmbFijXmS9SWl/WUcqs4C4WajJ+V+ZFQOrqxABLSzOFEKh7WTg+KuCfD8ajT6HuzR74U64ifprwX9JAjqdTna73aPY34NO/uiVK1e+sWTJkljyIv/LSEDGX8YL2jmZsFgsI5B1JHCY90HWQ/rgFFkHWYt21IP21E2Cid1q1J9VkJVoW13AsAKIlkOWoU59gon2x+kSi8U6p0E+Qh4ZBX3BR+Pkw0gk8iHOkYy/Rml04tqW80h3K/1h4zLYvBIseu12+0Zc74HjqPAZOhIt9dbb2zsMNg+ibCdWpYyvoI5KqOOLRkZGdkk3A+c+DyfCbnQ9/bxWjmGXjHb7GL/7TyslOrkdcPQ+j35KkwvwTJYHAoEajFFnzp07d9uXm2WKUNnnpHg8errBaNwO47cmxq2pihN1Q2BMXz5VuMmuow+dNlb0gvK9bSGxyB4WdgW++huDO+clr1UEE5lNgG1yTJYjk9mf7bW+vr6Azx+8wOmwr0D9Qs7ZxuRwaiGA+V/miqIWBVkPJpADAXYA5gCrTIPq4PjbDR3XE5j4XoMFXQOOudzTChNM4nDq/Nfn850CRt/v7OzckHY560MOqC0CmPx5USe+jDpB7wPaBc6s3fD5Mzi/O9rQQiwmP4vJ8+fg6Pr8mHwBDr49IXvh8z7YfxFx6WuV+2LC9yXQ2Y8Ex/uTII0DsD8wkyDcYoWEfglyG0Gei5H/FoET5EDIYjjvFtMeciDsO4AEjq/9SWDrIhLY9qUx+SL2ZOM+sHMfhNkLaX4BYRaOjo5+Bs6kb6ItKTJ5BotSbzLsu87lcgVQ/pqcvHu93josCL8K0KlJrg714TTUe83+Giac+F7Yy+/+A4RK2FCf/4e+KtPDPpo0H7ZKZrP5EIxbB2rSQAWNwjx5vs1q/2YoFKoYZynG6wTs/WshGDGXmJbxkAalDlNUHOcOiiaDMk14dcQg3gsaRVSm1LelQL8MhfFesTnM6tWre0aGhs9zOZ30letp4batVXwmXwLoT7nM8oWn8XjlaB47gsqx1LLUubGx0d7W1vZtDNB/RJQDsbgz4zjzSIcAFbjJtbW1Hjgx7oHT4rBPPvnkBQ05LCqwOJU1GQ68MOQD1Ikhkq6urk2YwPV/+OGHSVm5cuVGOpcS+pwSCpeSTOdS17q7uzdkkp6envXFlPF5pvRJ3/f3928k2bBhwyYS3MEeIFm3bt0gSW9v7xAJfU2ehI6XL18+QPaS7hRG2RIpbWpUznCIPoJFU2kVKVLusI2+MnhQe3t78pdDsSBug/NgH/SNmhwzsLCj7Xfo9weLhJSTVRkB9HshOH01+4M+43HTfA83Lqrsdvv35zQ2VtJ77cajmPTzwpYWm9VkukJI0hxiRl8znWmMiQXmqFhgiYrtsZ9jiol6OJ4MyZ+KmDS5srmIG30RjGevloPCdfq4OB7Ov+0tMaFTYESix4Bf85rFaIKeK8xMIEEjRHZPAGZOIMPZrtWr/+z1eG5CmwxnuMynmAATYALTQoAdgNOCedozkbB42x4DzL244307BvhWiI4mNtOuiUoztFqtcZfL9d7Q0NDXwOkiOHj4qT+VllWp1KK7fWg3fMevVAWgwnyxmL6+qqoqoELVClaJxgc4++ZizbMPJYbjg3FzZAadp89aE4vF4g0Gg9dqzS62Z3ICcAC+hTqucL8+eZ6lvErtF+PYXmGj8eLx7/gspV4qylsXtppPt9rtx+MmiK5KnxD7O0LizFqfuLRhVFwOuaTeIy6o84hvVPnFfrjWaoyLcl88URuAeFpbWz9QUVlkVMWpS4hDXCGxtz0kbAp89ZcyWR/Vi6VB84Rf/6Uw4AMfoD5KxwqKDOfjzxOx2BMWiyWuYLqcFBNgAkwgawLlPoZlbWilBMRgbm1ra/s/s9n8LCZ+X4bYIQrcL9MGQQzocn19vScej/8sFAodvmLFiheWLl2q9ACvDViTWAF2FbGAQj3hPnKSelBpl5YvX74OjuHn0KduXf81AiIQCJhxQ+QbGEd2wQ2Sb6CdmzRi2lZm0DgAp8jz9KTqVhf4g+YJ+Hy+29GGK2rhDXuNaNff6u/vPwkFzPNBQEhtn5/fvshWVXXJqMdjdetl6XBnUJxV6xX7OsJirjkuZpviYr4lJna3RcUxVUHxvXqv+Ga1T3zWGhb0pGAqnXLb6/V6gXrxX7W/65oY72ELi4NRLnUG5Ybdf/hNYn1MP+nznMlxIh6PKV22XV1dYV8gcKnRaHjXYFDQKKUV5fSYABPQLAGdZi2rQMNmzZrVbjab78RE76exWGx7INBjocqTPYCgDXf+41VVVe9u2LDhFKvVerGST/1R+izaIqDDHybI3Edqq1gLtmZgYODG6upqTT4FSHBsNtv+TU1Nz2Es+Sx91qJgjPQFg8GbtGgb2zQ5ATh9/4q6HZo8lLau0jwQc0K30+m8qaWl5QBtWZe/Naft2LBrtcN5x4jXP8MB599BzpA43h0QNfDJZJo40zk4CQWFO73GJz5jiQjdpC6k/HUrdkzUidjo6OgDCuQjK5BGxiQMkix2BuOjXUHRblTOZz8Uk8S/AybhjUtCypjz5pPkAIxJUmLzJ2X/0+tWRgcGz8ZaZC3KomgMldWaU2MCTGA8gXL9zIvbci25NL3pax2tra3HYyB5DneTTotEIi4MKMktLVjFHtIgXltb68UE+C5MeA7FwPs8P/VXsdUha8OpAVkslsnmh1mnxQG1QwAOhE/gGKb3Jmlu0k51fmRkxDI8PNzh9XoN9Fk7JbfZEhoP9Hr9811dXR9vPlPe/3Gfgvuo3IowDgfgf6ke5BatvENTW8bccCacgL9sbm6mH6cqb4MK1P7sXevm9Ztrf9ntjexsSMTEfvaQOKEqIBqMiUmdQpStHi1uniUGR2BQ1OkTdKrsBPXfhzrxgpoVnwmnHzn/drZGhQ7MldL1vaBJrIkaRXyKksY4L4PRVON83mqtWrv23xhrL8UNKfoxqrzTKVVEPTKmr8zXog2QsxYfeWMCTKBMCLADsEwKaiI129raqoeGhm6uqan5VTgc3gVOLk0u2iayf6rzmOjTV34/BKOv7Lrrrpdi0bdpqjh8nQmkCGCSrOC0M5Uq78ucgDw4OPgj9LnBMrcjo/pY8GzZMgYo85MOh8OHRddtMKNoCzukPW0bLVKnLTONZAQn9x2YK8WVMad8UqGGDbtnVVdX/xo3jQ+F5hU5vh29oG3BB6LpNyv9ic/JiZi0lz0iHV8VFDPgcMoWiAkB55jjYo5Z8W+IoliKvsmYG6/q7e1VYgwrSj9K7/r7vDUiPmcLCyNYK0UkkJDEv+EAHIhlt/xFmymqh7e7u/vJQMB3v9UKL6dSRhYxHXL0tRtj4gBHUJxa4xPn1XnFmbVeQWVlULCcimgCJ80EmAAIZNcDIiBvqiOgg/NvHwwaf4Cc5/f7qzBQ6SDcBaOo4LiRqza/rP+egYGBA1asWPHiU089VXETfqDgjQkwAYUJrF69+p14PL6E+hmhcNqcXPEIUHkZDIbXsej6X/Fy4ZTVTqCuru4Fo9EYoPqgdl2V1o/miLhZPAvzo9+0trScs2jRIoPSeag1vWuE0B2xfctBg0bXM/2hyOfRh+s+Y41K9OTfbFNM5LogcusTotYQV6u5E+qVSCTiPp/vNxMGUMGFNjiZ6D2MLr2y/sVlYYNYETaKsCxlZSXai7IKbJtrwmKx/RBl8jf0ScXOa9vcszxjlhJw8oXFGXD6XVjvFd+u8YuTqwLiYGdILIZQG5pnimSZGgdjAkyg1ARyHe9KrS/nDwLz5893zpw581pM4J7BBGbvSCRixCCV3WiG+FreaEKv1+sTNTU1H4+Ojp780UcfnV/sp/60zHMi21DfVDtRmUjnPM5zm8oDWqVEGRoaugv9TJD6nEqxuZztpHJyOBz+kZGRn8KOSui/YCZvmQgsXbo0arFY/pvpWiWcw/gtBYPBOldV1Y8++eTjO3EzuVrrdn9nYYvtf/ObLhqyNzy2wR+aH48ndPPNUXGCOygWWGIin6eXZPQiJOXEjvrBWCzmx/63atW7Go7VL9rDYnuUj5jia7oih78oyuudgEmsjdKXV7OKiBhZhSso0Pvvv+8PezxnIJEVaJvTkifyymqrh4P7SFdAXNXgEefW+cQxaC+ftUXETFN8yy8yU9vZ1RIVR7iCyados0q4PAOpqmzKE6G2tC5na9gBWF6lp58zZ85CDBAvu1yuS/x+fz3U5zIEBNowoZExqaeX898L59+XVqxYQe83Kb/bs2QMCxNgAqomsHr16tdxs+EdVSvJym1FAOX115UrV/5tq5P8oSIJYP50FW6gRivSeBiNeaSEm8dWt7vqTINO91pTU9OhGn0aUDp2+xnzPg6Yn+y2Nt7YP+KtIfNnGuPiWDgzPgdnhlnKb10flCXhT5TfFNxqtf6tu7t7BBwK3lCP8oM3Qc5GlMXO5rA4xBkUFp2iSYuusEG8HzIpWWYTWJH76a61a1dGAoEzwXOE1jK5p6BgDHi155mi4ru1XnFD06g4vcYv6GnM2ebYFqff+NyorPZzhMXhKLcaOHDHX+fPTIAJqItA+Y1c6uI3bdp0dHRUzZ8//3a73f56IpHYMxwOmzBQ8BNKKIGxwZJe7L0Md7VPWrZs2bnLly8fwCXeikRAp1N4ZlYkPTlZJlBEAomNGzfeXVtbipNXhwAAEABJREFUGx7rg4qYFSddCAEqH5vN5vd4PHcjHU3dFEJfzPMAFGquGxz4/6iuru6mupFr3E/Dl/8RnKB6Sa//DPqxJz755JP72tvbZ8EqLawNpMN3bqv+0pymH/TonUs2JoyHen3+5Ldl6EcLjnAGxH6OkLAWMJUZjOvEuuyfJgPW0m+hUCgAx+/FpdckswatcMyeXB0UjcZE5gBTnKV3/P3TbxLD497xF4Mvkd79R05AHE6RyubL0903rOzp+QvK5hZJEiW5MaEXstjZEhUX13vE1XD8HQcH+Y74XG9ICPrhm81UJv5PX9c+xh1IOm+duvzKb+LU+QoTYAJKEtDCIK8kD7WmJWHx8rzRaDwvEAi44QDkchsrKRqgIfR1hl+Dyz6rVq16CZd45AEE3gomIBecAiegaQLV1dXP6/X6jzRtpEaMw82zf5tMpr9oxBw2o3ACiaGhoWvhAIsVnlR5pyDhDzeVnS6X6xtms/mfTU1Nt7W2ts6AVVl/VxJh1bJJx+zWUfWlWY3fWu0Xb20y11w9Goo20Vd+SUE7HBP0AwZHwblhL8D5R5PMfjj/cvg6KWVfUsFcWXY4HO/B+b2spIpMkHmVPiGOgmN2Jzid8rmrEZIl8fiITdwz6BRvBUxb5bI6YhD/C5qET91PbMpY690lhPQ81jNUxcR0/NFTlwutYfGDhlFxTeOIONQdFu2muKD2kWs5uPWy+FqVX+xfoHN9OuzmPJhAJRNgR1IZlP6iRYv0uGs3D8LlNVZeNJHBxD0OJiuw/8bKlSu/i7vXg2OXp23HGWmXACZgQqfDakG7JrJlBRKgd4lt2LDhl1VVVZECk+LoRSSARVUQzp5fdnZ2cjkVkXO5JY2bqs/A6bWe5hPlpnsx9MWYp8N8qsntdl8AZ/nbLS0td7e3t+/Q0dFhQX65+gIQZfq2RYuEYc95rTN2ndl4wQcD4b9vNFf/PCwZ50UiET38m0ndTZIs9rSFxSnVPkFPKxWi3WhcJ1bBqRSSy2daDidvKBgMXlaI3RniKnKjNOmEsoTEUe5AXu9jJG/ZEq9JPDJkE4MxnVgP52xK1wQ0/G/AID4KGZOnkpUheTT1P9QdxJ46nFIhaIyKxWLnxKLRzmL3Sxa0hz2sIXEDnH7XN3nE/s6waDImBH0lPhdG422vMsjJNjbDEFPwDY7jc+HPTKC0BMo99/IZucqddIH6YxDishpjSINiIBDw+ny+h6xW6z49PT3P4lIcwhsTUJJAAhNmmlcqmSanpTEC0Wj0EYvF0kX9ksZM04Q5VC52u/09jBfPa8IgNkIxAl1dXWG/3/9TOL54/jBGFXNNCU5AHfYtcJyfgfbzT9xo/T2cgSfBGdg8d+5cM4IW4h9AdGW2E4XQ79naWrNbW8vevcvrf7HOF3vLZ7TfKsy2HaOxWPLrvqmcdEIWC8wRcUatT9Qq8LvHA3AyrQgrkFBKwSLvUY6y1Wr9ZM2aNW8VOauck5cQo9kQF2fV+YQlz5VOZ9AofrzJJcJCJ3RIkN5Jh2ST2wo4av8VtAhvHk//wYE8kQMwmXYx/nV3d28wmkxfgbN2I5Wb0nmQ429vW0jc1jwsbmkZEXs7IsKtT+TleJ1ItxajLLYzxwQ5dicKw+eZABMoHYE8u9rSKVyJOS9ZsmTaByC1csZgmMBCbiX0O23Tpk3fwQR+E455YwKKE0Bdk7EI4ranOFltJdjb2xvs7+9/wOVyleS9Pdqiqbw1cGKEMFbc39fXF1A+dU6x3AnAgf8g6sgm6u9zs0XboTH2wY0idEajscputx+M/cNwSLzj8XgeaW1u/lpra+t2HR0dVWMOwelaS+h3aWy07zJjRuucGU37vd1Yd/uqUPAfm2LilYTFeZrRam+VdDrDmO4i9UeGdJhi4rw6n2gxJlKnC9pvggOQ3idXUCLTGDkWi4VR169FlsoAQEJKbeSsO6vWJ5rgNBJ5PDO2IaITl29wi8DY05jklnXoEiIMS5eH9OKRYZv4d4D81iKnP+oTUJeQSk7RFAm8cuXKD81m83koM0XGLWoD9L7Lz1vD4pYmOP6aR8TutqgwSVIexLMz0Y0yoI6BJ9HZ8eJQTGA6CVDbnM78OK/8CNA4lF9MDcUiCBaLZZPJZDpbr9f/D5PPeW1tbTvirvQOM2fO3Amfd8Px7ji3kISOUzJjxozdxmRXTFx3IcHnXUnSj+kzJBU2fZ8Mi2vj9+lhCjneKt2GhoZdx2QX7JPS2Ni485js1NTUtCNkQXNz8w61tbXb19XVzYfMq6+v366+vn4u4sxG2Fm43l5dXd1GQseI01FXV9eMaiFBeJuCQCKRxy3jKdLkyxohkGYG+qRfwYmwBqd4rgsIKtronUof4KbRUyrSiVXJk4AkSYq3rzVr1gxjkf0rpB3PUy1NRwOX5GY0Gk24ydHqdDqPh1fw/nA4/Jbf53vd7/X+tKWl8ZuYG+2JOVgH5lM1mHvYxxyD5IuhdQbNN0imYkVhdIuEMCAty+zZs93zWltnzG9t3WV2c8Nxs1qbbh7WiVc3hMP/Csm65w0257kOd9X2VpvNocMfKTo+A0qw0RAXZ8LBNN8SG385r8+euCQ+DhnFcEKfV/zpjkRzZ5TfMjB9cbrznio/Kp+DHUHxRUd4qqAZr4+iLK7od4vB+KdlMRzXifsGHeLra2rFd9fVitd9VpFv40adUrzPyWhIhpOrV69+Bvn/Apfyrrg6RKYf5PiMNSKubhgRt7UMic/Zo0InEXlcLOIWliVRMnhFtIuTZgJaIEB9gxbs0LoN1IeSaN3OSe2T8Ic70PW4k/kUJuz/DoVCb2P/ZhiTQZyj/d8jkchfcW4JhH5Nawmu/YUkHo/T+b/gPJ1bgrh0/XWEfx3HryPMayRI/zXIn0lw/k9p8mccb5FAIPBnhPkT5FUlxO/3/yldoNerY/In7JMC/f48Jkmdoc9fkPcSTO7+CkfV3yB/h53/gI3/QBzi8Rb0/Beu/wvX3kbcd8Dp33BUPFdTUzNjUth8UaC66eHYsReIgmZZWpQCsWgremdnp294ePi3Tqcz74m6toiowxr0deGBgYHf9vf3+9WhEWuRLwGMYxS1KPMgjJM/QV3hr/ET4SkE46KEcdHsdrtrXW73Z6x2++l6nfFn0WjkxcHBwb8HMZeREomHPaOjdzQ1NPygpanpuy2NjSfPaGw8fGZLw0GtTU2LWlpa9m5tbNxjRkPDni319fu0NtUtaq2vP6Slru7LzXU15y2vq7k5Fgw8GPB7Xx70+/85HIn8JaI3PSKM1gtNNsdeTre7xWqz2XW4E0z6TKYy/bDE/1X5xF72/BxMmdLeGNOLpcGtf2QiUzg1nEO7oc2HueHFS5YsKcb4RPObvE2t18fEN2vy656DCUn8fNAploW3Lgt6ZG8EztnemEGQE6oQBVG/itLnZAksjip+g0gk3kB4Mgu77DZa3FPd38USSf6q750tw3CyRoRBoivZpVFIKFJ2CI7YYlS4QvQqMG4p60KBqnN0JQloIa3p6Qm0QIptUAUB3OTV2e12FxbaVQ6Hw0XHY3snjh1pQp+ddC0lLperigQT1+qqqqoa7GtJcFyXEjjG6qqrq2tJUucy7cfC1SOcIlJbW1s/ThrweSKhsHSN9tvknx6vrq6uEZ+baA+dG2BvTUNDQyM40fisijJVqxJY5Ngx+ToWd82/NHPmzH3b29v3mzVr1mIcH9zW1raYBAuZxZCD0oQ+k6TOLW5ubj64qanpkKZP5dAZM2Ychs9JwfHh2UhLS8sR+Ug2aY8Lcxg+b5HW1tZDSaBv0gayhwS6JG1saGhYDDmQpLGx8QCE2w/17Uv19fVfRJ3bE/Xvc/i8O9rRbmgvu9CezuH6Pkjns2ot/1z1Ql25G31Nb4kXDLmqrdnwVA42m22Z0Wj8nWaNZMMUIdDb2zvk9XqvRp8fIG+JIolWQCJoYxLmZHqzxWJ1udzVGANmVNXU7G5zOo+xOxxnWGy2K/RG448lg+F+Wa//XUzWPw2HwB/gjHohIsuvRIV4Ma7TPY/zzyUMhid1FvP9cPD9yOx0X2CyO062O1xfQHptLperxmKxWNHH6ilPkmzw0lcej3YFxNHuIIIX4gZC9LEtBhcA/fLvx+Xz/r8EeD2zevXq18dMUHSH9pI3WHo/3Kk1AVFvyH06Gkc5vOSxiNe9lqQ9eSuRjJ3zv2mLsGrVqlGhi56l1+mWgTWsnjprcvztZg2L82o94i44/g5whuH4mzqekiE2RXWiL6YXCVkSkpIJc1pMgAkoQoAdgIpgLH4iGMC5Dy0+5mQOxFqLkjROCCkWi4WxKM7vlquonL9EImGJRqPXgtdLOH6JFi34/ByOn8Xx73H+96BBP0DzDCZmT5Pg2tOQp3D9ScgTCPME9r/DuUexT8kjSOdhnEsKjn+TjSCthyaSyeJHIpGH0wVhfzuFkH7p8ijiPwp9HyOBHb+DPA5dkvaBwZMQ+orlU2MMnsWi8PdoQ89h/wL2xO9lhHkFx3/GuVfH5EXs74dz0YprZb998MEHw6Ojo4/D6ZTvt43KnoGaDMBNjgjK47FPPvlkUE16sS7qJGC1Wp8zGAx/hpMpi0W2Om0otVbo35Mb+nV6OE8Pnkaz2WwBWzv6RbpBm7wpSzdwXZtvyFbTscPpdNvsdid8fDYjvLAoAwOlkUwM//KxyyTJYrE9KL5R4xd5JpEx29GELvn0X3jsfXMZA6nkJMZjGWXQjXH7GqiUu5cNkYq50dNph8BBm08ePRG9eGrUlnzCL5/42cRRst5kk99EYbq61q5MREPnm0zGQSrTicLRj3tsZ4qKr1b5xY1No+JgV1gYi7jKj6KnHIzpRDfKoidiEPTV6xAcft64JF73WcQAHIAIMpG6fJ4JMIESEihi11BCq7SXNfeh2ivTklkE503C4XDESqZAmWSMyZ8ER6nJYrHYaAFDexIcJz9jQZNc1GCfXNjA4ZBc3ICtG4sad2pPx5Dk06dji54qfKaFj2KSSjebPfJO6pftfpwd2cZN2uh2u6sh9EQtPa3aUF1dnXxiFeeqka6zvr6+GYuT8ru5MUEdDgQCd2LB24e6w332BIym4zTxR/1aBUfCb6YjP85j2ggUrV11dnZGvF7vtejj10+2yJ42SzmjvAnohCz2soXEWXU+YVJwdKHKNwCHx38CJkHJkuSt5DRENBh0wXAweNvq1at7piG7nLKo1ifEqXDOmqTcKdIPsDw+Yhf9xXcwSegLclcwJxLZBV6xes0b8XDkZswvQuNj0EK+xRgXi51BcUmDR5xcFRAuPdXW8SGV+RxKSGIVnH5LfGbx8LBN3LnJKe4acIpHcfyix5J0zD7nsYqROGmmTJ6cChNgAsoS4NapLE9OrUIIlLOZmNAkBgYGijc7KGc4Cuou8d+EBFKYqS7CgaqZJ+a6uro2+Xy+p+FE0IxNqbIqpz2c8VGPx/Pkhx9+2F9OerOupSXQ3d39v9HR0V9gke2Saj4AABAASURBVB0prSaceyEEdrVExJl1fuFU2AkSkSWxLGQQvVH6bZNCNCx+XBpbJUn3D38w+Gjxc8stB4MkiyOdAbGzJZpbRIQmp99TIzbxV7+5qE//IavkBo6qcABCmTjuSP9KF4s8ZTB8+p1ply4hPmcLi29W+wT9kvICS6woX/eVsWLYCOc3vfvyj3Dy/QIOv9s3ucTTo3bxn6BZvAOn+O/glL0D5x4Ycoh1aCOIArV5YwLaIqAVa9gBqJWSZDuYQPYEElpyumRvNodUIQHZ7XYnVKhX3ir5/f7bjUYjOZ54/ps3xfwjwussm0ymnkAgcH/+qXDMCiUgx2Kxe/V6/duoR5rqlyqlPGebYuK0Wr+YYVT+HsxIXILjySLU3rHDaUV94LpgMHTNpk2bfGore3LQ0nsZ9Tm61jwJSfxx1Cpe8FqFL6ETOUbPGQP6gExZ5JyOUhGWLVvm/YwhcFWbVXrXKInEHFNUHOsOJh1/BztDRXnqLyELsTxsEM/D6ffQkF3cPeAQ98D592bAnCwDpWzjdJgAE5heArrpzY5zYwJMoNQE6CvAWCBjWC+1Jpx/pRNIJBLC6XRqqi729PSshxPwaTgB2YFQggqOmxux0dHRJ7q6unpLkD1nWUQC6C+K3lesW7du0Ov1Xop6tAamZMgPZ3lTJYF6Q1x8rcovdrbEFHcORWVJvBswif+FjKq0PaUUOf/MZrM3GAz+GGPRv1Lni7U3i9xQNxji4stVAVFnSOQWUQjx36BJvOE3i9EK/mrpbzv71hycWH/JMY260dOrvfJXUN/nmmNFeeqvN6IXz45axC8HHeJng07xnMcmVoSNIppzyaHwpnkj5waJqjy408yAs2MCkxGg9jHZdb7GBJiAxghgEZVoaGhIFGIW7ozywqgQgBx3C4ElS5aUV13aovnEB1iE3abX6zcihOZsg01q3uil9724yfFLNSvJuqmbQHd399s+n+9au90+grbMbVjdxZXUzq5LiONcAfFFR1gYJWWLjCZL7waN4okRmwgk1L1swo2nKP5+53K56AloZUEkSef/j776e5gzKHaxRnJ2WHnikngTzr/+qD5/BfKIqba57scHOGuPakgsOq0hbN3HEZWU/po7IRqNS+IVLxx/Qw7xIORfAbPwo96XgzPNrpPFzpaIOMYdENuZo2QOCxNgAhkIqHsky6Awn2ICpSaggfxpPqsBM9gEjRBQ1SJFCabLly9fF4vFntHpMBtVIkFOIysCNpstAcfNM/z0X1a4yjHQdPUVclVV1aOoS79CneL3Aaq8phiELA6BY+kQV1DYitDlvuM3iV8N2sXyiLqf/oOzKg5ZAqf1De+//75/moota7/QZ+H4O9AZEvYcV570NdT/BEzifThhw3LW2SliPnhOV58zqb7yNUK39uDaz1c3Nj3hqq65zBUYsuT6FepJM8DFEFYG9C4/euLvfjj+/uKziNHE9DpcoUbOG9WIGn1C7G8PiTNqveI7tT6xk7ko3TZllbN+HEE7BLRkSY7dsJZMZ1uYQGUSSCQSshafuqrM0ix7qzHlxOqt7M3Y1oBoNHprPB6npwC3vchnikFAhsN1PRK+B8KbNgnI02XW0qVLo2i/N0cikVeNxiK8UG66DKmAfPaxh8XRrpCoM8iKfzmxN6oXvxh0iI/D6v7lXzj9ZNTTzkAgcPl03gBBvlI2XpEafVwcDictvZsxm/CpahuXhfgXnH9Pj9rEutj0OqNgG3JPaZLcl+Rf35EttvXvNX/bUlv7pOQb2V94Bk1KKhKDlR/AuXof6jnV9Ve8VrEe9R6nlcxG8bSoHtWhXh3hCogL6z3i23D8fQl9Aen+rMcmuiN0a0DxbDlBJqAJAuwA1EQxshFMICcCNG7mFIEDM4EiEdBsXVy5cuVaSZKeSeCvSOw42TQCFotFDofDTy9btmx12mk+ZAJ5E+ju7h6BA/A8NOH34FyWhcg7KY5YJAL0pM+x7oBoN8VEMRY0vxuxiS6VP/lHjir0f+vh/PthT0/Pe0VCPVGyEryu0kQXU+f3hWNmZ2tUmKYMmYqxed8ZNgpy/tE+Ns1P/5EGGMNL6gfrPbSmVTLqf2awWH8cH9rQJiKhHAmSFZmFnqxcGdaLB4bs4q4Bp/gjHH/0jr/pfsoys3YTnyUAzYaY+LLbL65qHBWn1vjFXrawCCYk8fCQTTwIez4OmYTa7ZjYQr7CBIpPoBjjZfG15hyYABNgAkyg7Algcl1eNuSoLRwHt8KBMJhjNA6eBwGw3hiLxe7OI6oWopR0kaoFgBPZsHr16h44Vs5E3epCGOYMCGrZZsAJcDycfztbojm/Uy4bG/qiOvFnOEWyCVuqMOT8M5tMo36//7aOjo4XoMe01lHZRC49cskg5wm2dmMs+W5G+prmBEEynl4X1YsXPVZBP/5BP8KSMZCGT647quUzRofzaZ0c+7o8uskuCWU8oFRB1qNu/3bYJm7e6BLPjtrEMjhagwmdoGtqRtphjIrv1HjFTc0j4pSagNgdTmU9tCYn8Y9gy8s+m9gQM4i4mo1g3ZiACgiwA1AFhcAqlA8BLWiq03Gz10I5asgGtc8580a9atWqNVar9fdwTtFXnfNOhyNOTsBgMCTi8fhzK1asqMin/+AEmByQdq6WpK9Yu3btf1C/vhmNRql+lUQH7RShMpa4dAlxtCso9rJHhLlIU5rnRq3Cn5jcuaWMNfmlgnYvm81mfygcvhMO6l8uWbIkll9KBcWSJiNkgnNmsSModjRHRS7vrPOB+xKfWbwBqbQnueRrhG7dUY1HGXTy7+WA5/Mi6FPsu88h4uo1i2v73eKxEbv4JGwSPrU7/mQZ9SciLmnwiNtaRsXxVUExzxwXFkkWf0H9uLq/SjwybBcrIkYRgH0F1ebJI09W1SePyVfLnoDWDCjSsKk1TGwPE2ACTIAJKE1AlpW5o620Xkqmh4XZTeFwmJ8CVBLquLTgmBmE3InTMoQ3JqA4gZ6enjeR6CmhUKgb/RbXM8Ao1Ua/8nuQMygOgQPQoS9OUdAPTrzotcF9VSorp87XZDIFw+HgT1Anb+/t7Q1OHUP5EKYpxvA9bGGxryMiciknultG76R7btSWdE4pr3XWKaZXrqwjFRLwPwuFsW9p7cWGWPg3csjXJsVjijmdhpHUr4fs4vZNLvFRSP2OPwMcfJ+3RcSNTSPi5uZRcagzJFqM8eQP/XwSNohrNrjErbDl/aBJeODEpHpTCPup4up0OsXKYqq8+DoTKDYBdgAWmzCnzwRURgCLFx7EVFYmrI52CdBXCO12+x/4KcDilLEkSbJer//DqlWrVhQnB06VCRABIeAEfMtisXzN7/evwTg67c6BzVpU9n+avOwJp9Jx7qCoMRSnCOirv3dtcoqRuHqXSEajMRQNh38SDIZv6uvrC5SqVqAEqEgyZk/OmsWusJhtjooJA2WIuT6qF696LaIvptiDbxlymfoU2rig8WXqkMqEkBcJw4yWhusNicTVcjzmBjNshaedQBLLQ3px5YYq8Ts4VUemwVmGLPPeTJIsvmgLiTubh8UNcP7t64yIWkNC0PmRmBDUNs/urRb/DFiSDmKyL+/MOCITqFAC6h3dKrRA2GwmUGwCOl3hzR4TI0UmJsW2ldNXNwHUxfKpRwWgDAaDt4RCoeECkuCoExAIBALDWKTdjssyhDcmUFQCXV1dbxkMhpNHR0fZCVhU0pkTX2CJipOqAqLdFBfFGDzWRXXihn7Xlh/+KEYemS3L/izGzXA0Gr0rFIlcX0rnH2lsN8q6TM9F0SxzHzhqP28No5yyp0id+JqITrwVMFPyiJvcaf4f7JbW26rOlhKxc0U8ZsWYlj20SejEce3FUbM4p69a/C9kEnEVf+lCL2SxuyUsftQ0JK5rHhW726LCrpOTP+4TS8ji2VGrOKmnTtD7/kKyTiRgG29MgAnkR4D66PxiciwmUGEEtGIuO++0UpJsR7kQWLt27SqHw/ES2h7PWRUsNPCUnU7ny8uWLVuuYLKcFBOYlEBvb+/bFovlZDj2VyEg1u74z1tRCZA3ZIYxJo5xBcRnrFHF84qhFN/xG8Vl693i/ZBZtT8ioJekQDwavRU3lK5DPSzJ136zgb8DHLVftIeFUy9nE3xLmIGYTvzDv/nJri0nS3eQm/IF6LnuIMfxeoPxWhGLWAtIZkvUBFynQzFJXL/BLW7eVCX8idI+TblFsXEH1K7pXX70a94/bBwVP2oZEZ+3xwT9tIyMsBE4LN8LGMRZ62rEnZtcwi+X1A5SF1rxVmkEtGgvOwC1WKpsExNgAkyACaiJgJxIJG6Jx+MjalKq3HWJxWKeSCTyI9hBawXseGMC00MAzhdyAh6LOvhvvV6fmJ5cKzeXKiCmd4DRu/+UpEAdhz8hiedHLeKWTW6xMmJS65NFdL9jUzQeudhstd6A+qcK559NlqXxXhGHLiH2soXEztZITkUVQWH8L2hMfv03p4hFDAzoZF4RcxCi7yDHvka746dyOOiS8FdwZkZLoitu7T93XU3iTz5LwckVIwGCatPJYr4pIs6t8yZ/3ONAZ1jYxrwSYVkSayN68fMBu7hkQ434MKzadlkMPJwmEyg6gbGmVvR8OAMmwARUQgDzCxp7VaINq1HJBCqpLnZ1dX1it9tfxYICy5xKLnVlbCeO9PTfqlWrPlImRU6FCUxEIPN51L0PrFbrMdFo9Emz2RzOHIrPFkrACkfBvvaQOMEdEAZJuelLHD1xf1QnHh6yiQeGnaI/pheUOkmhOisZ32AwwEUpPpIk6aRVq3p+2dnZmZtnTUllxqVlFIQMIMXmPwPg7Q7H3wGOsDDiePPZqf+TB70nYhCPjdhFUK6cpWnfQc7t9Xbnr+SgvwnlmwOxDEwlnZBcNdGArLv75l7bHiOy4SNKkCRD6JKcIl3scBBvZ46Kb1T7BD3xd4w7KFxw8JNC9CTuRrTJl0bN4ooNbvHsqF0EUPspHl1nYQJMQBkCOmWS4VSYABNgAkyACWiUgDJmJcLh8O16vd6jTHKVnYpOp/OAJz39R2vHyobB1peMwMcff7zeYrF82+/332S1Wuk9n596Q0qmlXYyNkqb3wv2tSp/zl8nnYxCRJbE8rBB3DdoT75TbFilP/iBuhWNRaMvoFIdg5tIS2BTHKKazWAUkiSkpD70f4YhJhY7Q2KmKTc1vXFJPO+xik/CxmRahfyjd8mZUW/I0eSGY4meHqWnzehXZXNMV0okEkVbJ29cVO/Q2Zx3y5HwXLDDlqN26cGNZjlRP3OoJyid+29r/0Uvfby6Z54xcozToNuYHqyUx/QjHnNMUXEiHPk3NI6IU6oDos6Amj2mlA+OvneDJnHngFP8bNAlVkWMansat7AyGrOTd0xADQSK1rGpwTgt6UBPO2jJnnKzRUv6Svgr1B5MinggLBQix684Aq2tre/bbLa/cn9eWNETPzhbXl+xYsUHhaWkidiV1Ber0tbOzk6NH1H4AAAQAElEQVRfbW3tzT6f7zuolyvpiS1N1KwSG0ELlO3MMfF/NQExw6Scnz9IjoaAEc4/h3jNZ1XlE2fUx9ls1pGg33+XyRw+Fc6/lSUujozZtxgjkmHzg5PCqkuIPWxhsRckY+AJTtKTmP+B4+dFOAAnCDLpaZ2QRTUcfbNNMbHQGhH7OsLiYGdQHOMKii+7/eIrcB4f5QqIRfaw2NUSETOMcUHvnZuqM6HrKAeqhpPmn89F+Rqhi1nCF0h63b5SPFpQHpKzJhGtav5vZ++GxZ97vvuXJz21+RWWj32wZlVjwnOqzWwO5aOjUnGIYwuYH2APissaPOLbtf6t2jOV/3ogeHHUIn4C59/f/RYRgoOe4imlgxLpoC6oTSUlzOI0piCg1csFdTpahcJ2MQEtE5DwV6h9PBAWSpDjEwFUxYqaUC1ZsiQ2MjJyh9ls9pP9LPkRMJlM/mAweCti5/aYCSLwVtYEVNtfLF26NLp69epnUS9P1uv1f4WjP1rWpEusPBV0oyEuvlwVEDtblENJzr+3Aybx4JBdvBM0w1MijT2/VmKD07JH/UlYzeblIb/v/Kpw+JrOzt6htMuqOqzRyzqdjJEcWrWRk8cREpYcV5a9Ub14ZNgOR6yEVLLbyOlXp4+LHVE39reHkk6+C+o84vqmEXFD06i4tMErzqrzia/DeUxPmp2L46sbR8VVkO/UeMWhcBDONkWFAc7DiXKEVZLRaMzRmolS+/S8vEgY1v3DforRXX+R7BsxfnolxyM9PK+NbWGvZPjVJq93/wPeGHh3fAqvLN/wsjnsvbNUNyXoCcwvWMPi2zU+cUmjV+xgiW2lYgjO+A9DRvEQ2uOvhx2iOzJZiWwVddo/oF1mX0GnXTvOkAnkRkDxji237Dk0E2ACJSAgLVq0iAeyEoDnLJkACNCPB/wdTnQZx7zlSIC4wYH6xowZM5bmGJWDlz+BEoxbOUFLdHV1vRsKhb4cDPrvrKqqGsCikdt5Tgg3B6avbB7jDohFjuDmEwr8J+ffW3D+PQpn00dhkwIpKpsE9W1Oh8NvToR/awmOHO3Uh1+y2Yzbn7J769xrhFDlem2mUUh6nZCsOlnQu/+2H+fgmYoQOYCeHLGK5WEkhMASZLKtRp8Qu1gi4jA48P6v2i/Oq/OKKxo94mvVAbHQFhVu/cTNTYfEm40JQT82cQ7ifafWJ/aHw5K+KpwxT0mSUCaIlfFqziehmUTv/FvvbrzHVF338/hIvxtZ5Je+0SzLDe0bhgaHTl8qrzz7M891j0ygkOw3Oa4xSYlXcF25x2iR2GSbSZLFAnNUnIQ2fFHDaPJr4aY0S8FCjMYl8Ve/WfxqyCFe9lqFN6GbLEm+xgSYgIIEuLUpCLOISaV1m0XMhZOuCAJ5TzjS6FgsFq6TaTz4MHcCVA9JEFPddQkKKrl1d3eHPB7PT+x2e0DJdCslLfQ9fp/PdzM9TVkpNk9mp5IL1MnyUcM1Hf7UoMdUOqxcuXKjy1X1g5GRka9ZrdbXbTaban60YSrd1XCdBoSD4Pg71h0UeoWezyNH05t+k9js/DOqwcytdDCZTImaKldndHTotDZj5IcLXdJ+P/7sjH88vm/dmzfvOXvpbkfv9POfHbB97VaRVPDBoBc6nZCkJkNUHOgICQMVXg56ve03ilfg/JkqSo0+LvaxhQQ5/c6u9Yrz633i+Kqg2MkSFWbdVLG3vU5PKe5jj4gz4QQ8xhUQdjgwx4fS6SSdQYF3AMLZJa1d7NpuwzFNPzU0NP9NFw2fnvAM2sfmP+OznfqzxZFIuOreG+ntPnL7F9Y/etJTYtIn4Ts7OyMxafhkl82yDOMF1Jk6i3xDUPG3GmPicDhov1vrFeSYbTbKgs6n0iQvZF9EJ54esYkH4fx7L2iCAekhUiHVtQc79SupLmSsjYoJ5NFtqtgaVo0JFIEAJ7ktAR4It2XCZ/IiUJETqkAg8Hez2fwftCM5L2oVGol4WSyWv2Kx/G6FItjGbCwiK6IOwU6B8tdvA0ClJ+grwV1dXX+Cs/+EYNB/ldvtXmcwGCqirAotks9bw+KUan9Gp0w+aYdlSfwdzr/fDtuTPzKhpkGH6nVNTU14phx4qHVk1XkHWgOtd+3q/tN1s+Wfbz/48TxD33Kzsetd555i4xlHzzS/+r/DG3fKh0Gx4hgTso6e/vu8NSrmmGM5ZTMSl8TDI3YRmuRXf926hCDH3zdQH74LZ92x7oDYyRoT9IToZOUYQ0ujX3j+IGgU/0LZvxMwio2xbZe8DcaE+Gp1QJzgordyyFt9IViHwrEaEgX1ORsOb5jdf1LHLywzZ7+pS8TOSmxcVyclYpT0ZOpPyFFyVMcSZutz4eDAEdv/efQ/EwYcd6Gzc5MvMDh8jMvpGEY/CjrjAijw0Ymy+qI9JE5FWZ1W4xO726LCKG2dVRwf30eZPADH3xOjNrE2WhBeBbTmJJjAxAS0fGXb3lDL1rJtTIAJCMxpJK/Xm9fkg/ExASZQOIG+vr7A4ODg3Q6HI1R4apWTgs1mC4yOjt5GTzRUjtVsaYqAXq8vu3Fr1apVoytWrLx9YGDgKJ1O95TJZAxgAZ6AYCmcsoz3KQL0Qw701cwmIz0nlDqb/54cQf+EA+g3ww6xIqKeJ/+o/C1mc2JuS2PfDN+GX/5fQ9R0/Wdb7r9se/OtjqG180TQu8VJRHM2EQ5KxvUrd29wu55aDqdS/kSUjWnRS/EOY0Q+0h3M6ek/2C/+OEq/+pv5q9j0Ix0LrWFxOhxJ36n1i6PcITHLHIdDaWL9ZVzywKn4V59Z/GrQIe4edIpfYP9LOJseg/N3VdiAENtuVXpZnAwn4HzT1u+a1EmSZEnIeXmo+o5sqes7seMuw8xZ/5aioW8n+lbWiUh4rExz78bASxY2Zywaiz1iCHq+2fHswPptLZn8zCfr1i33Dw18z2KxRJLpTR4866t6uE3ngd3XqvzJd/0d4AyJGtzrGG9lBAX0itcy9uM7FuFP6MT4MFlnWpqAZaZuaSBxruVBQFcearKWTIAJKEUgOZksMDFMHmggxHBeYEIcnQlUKIFYLPYGJuL/RVvidpRFHSBORqPxb6FQ6J0sgnMQjRFA+dP7uPJajOePQrGYiZ6enndHRkZO9fsDXzebzX9NJBIhCDsC0xBX6xNw+HjF9patHTFpQXI6pI713aBJ/HrQLlZFMjt/ckpQgcCoxzIcwfHZLY3e/Vsc//labdB/y+fqTl1cJ75aP9o7Swp49BPN0ei8NLJpflXjjBfWHNn0OQXUKTiJZnNUf1xVUGo3Tfot1G3y+ShsFM95bHAdbX3JLMliV0tE0BNkZ9f5xOGukJhjjgl6p9zWIbf+FIa/+M9wLv14k1PcB4ffk6M28RefRfwvZBL0fkEZrqbJ3g9I1w6C4yo9VfLWmfW5PQG4+psdlv7jZl5lamp+3yDHz413d9aIoF8nC5hqr0pI1Q04TM8lu2PJYpdloXs1MhL4Xv0fB7zZxdo21PI1634bCwcfQF1SpO+pQps9xBkUZ9Z6xTFwAs+ewEk7EJOSTtlfo2yoTOIoj2214zNMgAlMFwF2AE4Xac6HCaiIQDAYJAde3hphEltQ/Lwz5oiaIoBJqLrrURFpr1mzZnhwcPB+h8MRKWI2mknaarUGh4eH7+zt7VXuVwG0QSevBWU5mg7HSVnPWenJXzgCn4ET+1g4//4PDu03o9Fo0hFYjuWhpM7k4Dmlyie+YKPuUJlhYVnIIO4ddIiV0dI/+Sdv/ovVuF2DM63S785tDPdd0BTYfbHYMLeqf7lT+D1Z1W0pEZcS/d3bWxqa/rD2+NbDlCyDfNJqMpsP/mKNwZpLiQUSkqCvY2+K6be4gfRIYDtzVJxVR+/384qj4UyaD8efhVxnUyj2CcqZHH/3w7n0Bpx+9EuyERkJjsVz6BLJHyiZZZr8K8pzTNGt+lKanxh1+qw8x/KJQr/uMPdie8T4tmQ0XBNfu7xZ9o1sLlODMaFvmNkt6aUHJYMpPKZW9juDSZaM5rd1fs8Fs5aMjGQfMWNI2WxzXOKw297KeDXLkwY4ahfAUX9GrU+cWkM/wBIRTv1W+JIpodonv4L9o40uOHytog9lnrzA/5gAEygpgc2dU0lV4MyzIYBOdNueNZuIHKYgAlqMTJOaQu1Cffx0dlVoYhy/0glUbF3C4v9lp9P5CdoT9++TtALiYzKZ3gSrNycJVsmXKqL+lLsDMFVB6WvB5AiMx+OHkyMQdr0TiUSCVM9JUuEqaX+UMyAWO8N5/aBDJk69Eb24Y5Mz+fQXDTAkmcIV+xyVZzwajRl1YsNCR/yOw62jL9y5g/GYPcTG+fZN3QYR8EA1bDkoIsVjUmJDT5PFWX3f2iNqP59DVEWDrjyw2j3TZfqm3SDpc0n4Na9Z/C9oEomxSFYpIY5z+cXl9R5xmDMkyPHnyMLxNxjTiQeHbOLmjW7xJ59VrIvqkea2LOnXaPe0h8VUzkR63yA9ZjymlkjOlXWJSR2A6HildQfVzOxPtDxjqm56ITHUv7M8OvDp2tpsi0pWx+9jm9bsER0auCcRDuT8eKtktvvi0cgN9S7P6pRuhezff/99f9g3fGZNlWsd1c9c03LBoUo/nHJRnUcsdoREszEu9NK23IdRPg8MOcRPB53iXwGzCCR0YttQueZe0vBlrn5J2ZVd5lpXWKd1A9k+JsAEtiYgSVLB7R6LFkqDB8Ot0fKnHAmgLuYYQ1vB4QTYMDg4+JjD4ch5UaAtEpNbYzabQ6Ojo/fQwmXykHxVywSwWKVxRzMmdnV1eXp7e5/BeHqITpZPQ3/4bjgcDuKzIl/PKxdQe9rC4tiqoKgxJIQSk4qBmASnkFN8HDYJOGhKggF1VY7HolERCfVU68JXtMjBb142z3LyWTPF1xtG1tiFb6QgU5NOQM9gi8Fdf6m8SEzqpCoWAIsh9h1rItqeS/qDcAq94LEm3/9G8eaYouKaxhHxjZqAmGeJCfpxDzo/mYQTknjZYxZXbHCLJ0fsoitiEFE5M85mQ1zsDyfVVE//UX7jfglYoD3qDHHdhGw3Lqp3rD/IfYWxtvo9KRI5KjHYZ5LEZkVkIWRhdQ1Lkrhoo1jz1eaXfZv0Ol1UkkWC8spF5FjIZqyqv6XfsN19vcc2X9h3ZMP/rT2i4dgVh9Z84eNDmjpWHNpU/8lRdc6/5FAPPl659sPAyOAPnA47vZMU6k6tEXl5dzRHxBUNHvHNGr+Yb4kJawZHLeq+eDdgFDdtdIqnRm2iB+WTEJnLZ+pcVRUiK06q0piVYQITENDUZGoCG7VwmnpO7ni0UJIqsSESiVCdylsbDPAFxc87Y46oGQKoQ6k+rZLrkmw0Gp+qqalZncZDM2WshCHExWazvQPHyF+VSE+DaVRM+5neHwGZvppCTwR2r137OMblxVhk/x8cgK8Fg4HBaDQao/pPMn3aTG9O7caY+Fq1X8w0xhVxEfjgHLoNjocP4PxLCNA1eAAAEABJREFUTK8pgsqJJBoOBa0i9sEXLP5zD3DKX/pyfbzqoV0Nz7UE+tvkkU26lJOoYPWCPklvMHy+397QVnBaOSbQd1hVuy4RvxAOLlTZ7CO/5LWInqhBGKSEOKnKL25tHhZfsEcFvf8xm45sedggbuh3ip8PusRHIZPwJiZextL7BD8P5/KX4AA0ZpH4UHzrtCT8GfTb/gjIhycKU/cBjv3iDnmJ3ma9DmVaK6LhLTnIQpIle9VH0bB//4bn+u/e6SkRIUIJQ8wrWWwBOs5JIiF9fHDdTlLId6pJ0t9mMJt+bTabnnDZHUtqnJZOl8uyurq6efmcjs+80Hn0jK93Htfe/OSJYspy+Wjlmt8mQuEHDAZDbCp9rCIu/1+1T1zXNIryiohqPazMEMkbl8Qjw3Zx2yaXeCdoFr7E1kwzRCmbU7BkSxmXjdKsKBOYgADq8wRX+LSqCNCkQlUKsTJlS0DCXygUKqjtw2kx5eSibAGx4tNGAP3atOWVc0bTFOH999/v8fv9T9vt9ikn4dOkkqqyMZlMoaGhoft6e3uHVKWYSpRBG6qIRQmGLZgqFzRuqaTIJlSD3gva09v7bCwWO8ZoNB0cj8d/HggEVmC8Tj6lQwAmjFyGF5y6hPgGnAo7WCJCr0AtDieEuGuTA44Hi4htfhBrWqhQucBpSw/8jTabpdd2sYrjdzAH9t6/Vv+/c+aZXvl6o7jM7B+2SvGYAlammUQ/C6HT1wmTtCDtbNEP6X13Op3uHknSNeaSGX1FdwkcgDuao+InM0bEd2r8oskowxk4dSpBOHafG7WKa/vd4m8BiyBnHYp70ojbmWPiaFdQuLKcra6JjHvYD51OtUFnTM/k40OqOmy+mscsDtvLUjyyuwj5dfD8bgkiRyNhOPkeMQT8e7e+NPLfLRfoQB/aJPS6Pqov9DFrQTmLWFQScPjKgVGd7BvVy/5Ro/ANm4V30CpGB+1i05om88Cag2ql+EO1BtH1Of28375/eNvsKZ4KTNj9/sscFvM/oFNGnDgv72wKy3fPGBZfT5ZXQhglOaPqH4cM4qZ+l/jNiF2shZN3OttgRoWUPqmD717pNDk9JlAiApqeTJWIaTGypddSZO5xi5Ebp5kkoNV/mLjpXK5sp0SZKWCyS30H18vMePhsDgQWLlxIdSmHGJoLmvB4PA+73e4emmxrzroCDCIeDofj3waD4U8FJKPpqNSfC1ogatrKzcahHoxboW8+r7X/cHYHe3p63sX+QrPZ/CURj5/t93r/iRsFw1p5KlAPJ8KXqwJiT3tEWKTC/WIxtIF74Qf5q98qInD+FZ7i5LWK+iZIgp72M8XCq9pM8r0LHNL+BzZ0HbZDfegfB9Sazj2iQfdGY3hoByla2A3XTJogb1nGRCzhH/UmhM6UKUyxzm30Oo8S4eD+Ev5yyaNWHxfXNY2Im5pHxc6WqLDq5Kyid0f04taNTnH3gFPQ10nJsUTlSzJRAvTrtHvbQmKeJTZRkG3Or40ZhJxmE01MdrRuLrtfLmyxPfC5qm86DIZ/2uTYcVI4aMYEeIsKMv3FooMJs/nbDcYNp9a+POQZn0Hz8yIoB/1v43x2hiNg1lsiIUQsIsEhKQnPkM062v/lBuH7ZHbj3MeXHVE7Y6J03urtDbqiI+c211atJRPSw9mlmHx+rUfc3TostrfGJfMWa9NDCUGO9xdGLUnn7N/hnA0mdEJ5A7fOsxSfUDUqYvwpBVu15VkJ+lD/Vgl2lrWNixYtmqDbLWuzWPkSEcAgJkUikYLaPuadWd5TLZGRnG3ZEBgdHS2oLpaNoZMo+sknn6wcGhr6o8WSw2plkvS0csloNIbg9Hho+fLlA1qxSWk7sGjLchmtdM7Tnx7GrkpbgCW6u7s3rNuw4Tc6g+FgnU53dDgcvH9kZKQzGAwOx+PxKMo/uU1/aeSfI01oD3aExEHOkHDp5fwTGosZQxIPD9vFy16bCCQo9bELCu+SoGU5EY/FApFgYB2cYC8vtEe/tb1V3mPJJ6vPfbmz579tsYa2kxprXj6mKny9LuizKumcl4WE3BOhhJzok2qa/ym56n4SCQS/0mxY/weFTZ0wuaEDq93CYLxd6PTmCQNNcMGCkX6GKZH8MY6pSklGGkGU5Ssei7hyQ5V4FSiDspS1Y6nJEBf7OsJiqnyQTXJLyLL8r8BmP6osRDIfGR5WixDSA/s07LpLnfziYbXx++HsbYEWWyUrS5IsjJZlUYN1/5aXPL+VnhJxkeEPkeSEZ+hVXVW9T8ZfhiCKnZLkhCTFo0bzSP9xbrOt8+PDGr/6n4XCmCmDv3Su+ajRs/7yuip3EPVV1uPf3ragfF/rkDipJiQZdeh5M0SkMuqP6cQvhxzirgFX8qk/OpchqCZOSbJcaeOPJsqNjchMAN1x5gt8Vj0EvF4v+h3c3lSPSqxJGRPAIkIHB2BBDjy9Xp8cCDEtwJymjGGw6iUlQPUnFlP4a1EltSjvzOM+n+9+u93ejRS0PIeGeVlvssViWQpHx0tZx+CAmiaAcaegcSt7OOoL2dfXF+jr6/vHhg0bz0G7+JJelo8NBv0/HR0deQ996CZoHIZPgfwYqu4/aNGxqyUijoKvocWY0U8CU7Lf6Mcfnh21iec8duFJUOrZx80mJJjKOp1Eb4fzJiKhlaZ46JkF5vAZu9dIe+3e23f0s5/0P/b7T9YN/nTuXNMdezYfs3+z4y+twQ17SdGwInWV8hd6Y0IIeVDodK/FzdbThE7arSG2YlHD0z0Xtb7Q//pEDqds7Ms1TNSivwresQ4au3ONm214cujSU393DzjErZtcojtiSDrysp1sWuGP29EcFR2m7OvXmoherAhv9o9RPgb8q5VDpt3a6u88tDrxr9mSb5FOCH26DViVycJgiko2xx+DhtieM18Zfj/9eqZjvcn8FyHp3xY6BTzfmTIYd06ShCT5R1w1Zt1vG7bb7t4Vh87N6Lh9+pO+x/c1DD0y36GLXVw/Kq5p9IgOM1yb49JLfYygl3k/aBS39LvE4yN2EZCl1CXN7mVJ0mvWODas4gigP6s4m8vO4NHRUV0ikYjTRICFCRRCgCq/Xq832Gw2Nx3nK6iPWH8UognHrXQCY3UPiytdxgnp2PXS7EqQ66xZs1agr/+D0WiMVHrdIPvp3X/g8fjq1as3lqA4yilLwqV5oQKxWq122le4xNetWze4dv36v8EZeGmDxboYE8QTIuHwHZFQ8C1UhHUY4wOSJG2ZM6qFF7kIWo0xcbw7IHaEE7BQvSKyJF73msUzo1YxGNMJSr/QNMFPBjvZZDRGLSbjqFFOrKiTw4+3SaFTd3Aav7hT97qvvLhs/SN/+LBv7VNCxCm//yxy1i1e4L/pK82631o8G2fijn3hqkhIwmSJS0ZLP3wt98ckw+LG/pHDZzw/8Dv6RVnpKRFHCFwiDaZHNh7smisk3akiHtUVI0cyxhMT8t+8Jvm6DS75uVGrHJJhZYbMqJwynE6eqtHH5C85QsnjbP5RWm/4zJR9Mjg5ED9rCcoXWdcadgqsnaePhi3JC2n/ZDjwJJtzNK4zXLdJ13fSrOdGRtIuT3hY/8cBbyIw8jOdq2aTLOm25DlhBKUuBH06SyJ6qrtK3CJfI7Ytv2uE9C33yJs/nR+PHOYKCZs+c3OCJ1oMxST51VGzfEO/S347YJIFAJJgp9mNisFsNjppz8IEtEBg205AC1ZpzIbW1tZ4KBRaEo/H345Go2/i+G+BQOCvwWBwid/v/0uaLKFzdI3C5COI//dcJZ98ComTrh+lk/qcfpw6l80+HA7/HfKPdMn2GHn+MxKJFCzj85tMb+T5t0xCcZDOVrbQORKE/2csFntbkqR/Dw8P/xU60zied0tBPdw0NDT0Z9TJtyD/wOe/0x7p/iNNklygU5It6ZEu0ClpB9VXElxL1l+l9pRmvpLSrZA95U3xU3s6Jkl9pn02QnHSJRUn/Vy+x2Cdc3unONnml9KV9oi3hOJRfUBd+Rfq4rtUF+HwKqgu5l2JVRZxyZIlMfC5x+Px/Ab7vxEnMPs7HdOehM5lErqWEgqfEuKeLgijaBsbn156XikdUnuETdY10p+OaY9r/0Tf8SbkLfRP/0K9eBsriP/gBsPbuPYbnH8OxSRDeJuAgM/noz79VWIKofL9C+1T8wI6TpULHW8jfv+SICQVHum9kSav4zhd0q9tOU7FnWifnmdKF9qjjJNjAO0RJlnX6Th1LRwM/h2OrX+gXrwTj8XeRJhlE2Co1NPxzt7eodVr1/59zdq1V0l6w5EiGj0O5XlFLBx+Wpbj76F/XQdnulen00UBCc1r84bjad+q9AlxpCuYfO9foYsPevLv3wGjeGLEJnqjhrxs2UxClsEmYbVYwnabddhhtay2GQz/rJMiv6gXoa+3mxOLZuy25huvreh95qXO7g0ppx9l+OSJQv+v/eoWuqurH6tORM7TBTx2jGuZPVYUIRuREN3qiAubs1eWE/ckpMSBjbahs2a8PPKetFRQGWaTiuJhZDiNZKvlR0IkqhVPHAnKcKhFbNUjn8TtH/xm2P5vv878fpvbvLzNZVpmN5s+shoN71sNhv9Z9Lr/mnXSe1aD7j2LXnrXapDes+Gc02T4oNZm+bjZaVu9q9sQ3dkSkZFsVls0IYe6w/r/zK+xr96x3uE5yBlK/KDRK9F77zImYDTLOrt7RTzg+0rzH/tvTP3Kb8awGU42mgdeTPi9V0l2V7ew2GMyHIHylH9wsYGRMJoTiJMQNldC2EncMuqKLMw2WRhMsqD6kyFPOiV7BiXJbD173b+ce9DnlGw6qs65/t36a6vd7p/bRzfa9JIkpa6l76mMQhbX0PsRy3v/DRnfGZWN7zkN+v/ZpMQ7pmj47zG/741YwPs6SdDnez3k970B+QuJH8d+n++NgM/3us/rfc3r9bzm9Xj+TOIZGfkT5j1/pv0IjtMleR7hxu1fS8ZPpkNpJYXSTR+rPj32epPXvF7va8g7ebzV3rf5ug/7pH7Yk74Yb5ZEI5G/i0Ti7UQshvVMeHk6Dz7WJoFKsarQMbhSOJXUTloY7rTTTqfZbLYv1dfX79fU1LS4paXloKqqqkNqamoOdTgch1kslsMNBsPhmMgcLknS4VhMJQXOmCMmE7fbfUS6IM3DnU7nEVMJdDkyJVar9ahsxWw2H12owNajUkJpZTpOnctmj8nxUZAjxwviHpF+jj6PF9h9OHTIS8DvsJTY7fbD7WlC5ZBJsBA6jMoWc4XDSFDWh5Jgkn8IdD0YdeBg6LiYxOVyHYg0FpOgjA9A+l9CmC8uW7bsG6tWrVpTSKXu6+tb293dfQpkX8h+a9as2b+jo2P/hoaGA1B3DgSTxXq9fjF0PAj5HDwmh2J/KNlAQnaMyZHYH4nF/lHZCNI/OhuB/UfB5qPzEZRr1nWawqINHjleKH86l9rTMUnqM+2zEYqTLqk4k7XrbK+B9+EkVB65CMor2dtWPegAABAASURBVL+gfJP1MLWnupgSqpOoB4eC/yHV1dUHox4egnp4EPQ/EPVgf6SxD859GfXRi8+8gUBPT8/qdevWnQk5sLGx8YDm5uYDiRn6iYPQdlPt6GA4yA4hQdkdSpIqO/A8HOGTfTrVATBOtq3UHmG3aWPZtKWpwqTSTeVD+7H8t9QTmHcoxqdDsD+YbDEYDAehjizGuQPxeX8c74dr+8GufdF3LEIfdU5vb+86nONtEgIbNmx4Ze3atcfO6+s7AHIQ6swhqAeHoM9IzgvA93DwPAzt8nAkcwQJ6suRW0SWj4xD0IaPIsH1o9PkGBynS/q1LcfI48jxgvI9IiW4lpyb0J50oX6BBGV+SEpQBw5GX5oU6i9Qjw9yVVUtNlssi6HDIqfLdVBXV9ejOOYtM4E4xuHhVWvX/ntNb+9PZZ3um5aEOBx3j0+IBnwXJ8Lh30hy/K9GvX4ZWA+iLEJoayh6OYFyT26Zk1XmrEWSxT72sDjAGR778Qcp74TjshD0i6Pk/FsR2fy1zakSSxqIfwgnow7GHXZbsLbKPVDrtH1cYzG+Wi8Fb2uMeb7WbPAfMK/Ke9D+nSu/95fO1X989eOe9U89JeKIt9W28sBq966+utPaam1POoPDB0ixSH5eyPRULfaE5KztTUSjd8UjocWNCwcvaH5+6CN62i89WCmO+990LJL05oNFNJx/wU2kuN6YiFpd/x3xh05sM8p7ndyo+9LpLZ69z6wJ7fEtm2/PnRyRvbaXdHvvZjLtPcvh23tBbXDvzzZHk3J8U3Sv45ojex4tdJ/9bo1v9/Orhr7yvQ5Dv0knZaUnqoQcjcWWntWU2P8aV8/iW6vX//qSGSFRY0Aly6SvxQHHm/OfsZDv6JZXPa8gkwkCZoq8+RyVZ+NnBx6IBIaOjocCtwideFnoDW/Jkv5dodP/T+h0/5N0+vew/4+kM74jdPp/SQbD3yWT6TWd3f28cFY9rne6H9ZX1T+sq2l4RFfT9JSuruVlXW3zm1JV44e6muZhGU7Fzblt/V8e7jca3Q1Xy4tEsr5uOMA9WzZaHtNLustk79DEDmyLPRoV+iXDAf+xM6OmfY6vMi9abPbse1CdtO/xrdYDD5khDlngcB7++eq6pFTV1h7urqk9zOpwJsVisR5hsVqPMFutR1pttqNsNvtRNrv9aBK703kMxoNjcO1Yq9W6lSTPm83J6xgrjh2TYwwGI8SQLkfrDYaj9Xr9toLzRpOJ1nlH0368WK22IzG+HOF0uo6oht61tXWHz5jRemhVVdUhJrP5IHSS+xlMpsWrVvX8bmua/IkJlC8BdgCWSdmRE7CzszOydOnSaEroMwkmxWE4YUK9vb1Bkr6+vkB/f7+fZNOmTb7JBPF944UW5FMJ8vTkI1jUjZaLkH3putJnJWUyxuPLJPWZypSEypiEypuEyj8l0DFMgjiRTIIqv81kFufy2ejJLUorKVRHqW5SnpQ/6UO6pYT0JSH902Wy+pnp2mTcxl+DHnnV01zjjc93Oj5nYpPvufTyyOWYyjNdUmVNeyp/ErAMU51IFzpPQvUln4qn8TjUrmLEhoS4EUPiRVxJUszHlxXCbunPs60LStTVifJK6ZfSl3QnIVvShewjSZ2jY5Qx9SvY8TYFARnX40uEiJFMVWeoLFLlotSe0hwvVM4TSaqcx++p3ElQj7eMXfSZwtE52Em2YlfMTRNpy0lm3d0bVnR3v72ie819Jrv9bEs0flwiEjk27PGcGvV7b5Rjkcd0kvy60WB4z2I295jNpiGdTheEczgK5+AWxyA5SQqhokfkHcxRcZgzKBoNcXzKf6MKsCaiF0+PWsV/QyZBn8enRvqSwBkQs9tswSqnc6jK7eqpddr/V22zvO4ySL+tjgWudAS8J7ilyGKLx3/cvh+svPpPnT0vv/Lf7u6n3uoNXiME9cPjk6b8pJWLXdsZXI67qqymO3XeoVlwAhW2ljKaZMlVs1GORn4a9XgPbHph4OKW5wc/ka7JrMM2ShX5xNqDXTW6qrq7EkGvTfGszNZ4yOJ4w+MLnrTLqxtf2/VP/f5Tl3SHvo79Sa+tGv3aP9YMP/p2l+epzk7fb99/3//80r4Alc9DCENyDfZJwfpoEZTbzR452zq8tgWHWW+2ukZzXcJ74AxD/P6qyOhZIhrZpjxl/El2dzShN/5e8vhOpPLJOoMMAalsZ77k/6D5Fc8PG/2jRwcDIweYJP3+0YR8QCIoL44k5MVhnXSwMSoO1huNB8d11kMDI7aj+uPmkxpCn3y9dlbn6bUz/3t63a/e/Ubd/e9+ue6B946om/Xhvn5z9HOJgOdKyWzNkKsQcjgodBbL/uvNtlP7FluP1bkdLyD8YSIcMGSKQI5E2VW7wR8MXO8LR07c/U8jf9sL7WM/cL/9/X7/rf9c5r1mSafvTpx7uasrjHKKkFB/TUL9Nwn1R5PJRGNF+vnxY0wun9PTGX9MepGOJKQzyfhxlK6BjwzhjQlogsA2nZwmrGIjmAATYAJMgAnkQ4DjMAEmwAS0RUCmRe0Ha9YML+/u/mTVunUvrF63/iarw/UtWehOMMS8x4T9w8eHh0dOg5PnB4Z4+F6TEM+a9bp/WEzGj21WyzqrxTJkMhp9OkkKwzkYgyTgE9lqy4RMwskmQ0wc6gqKHS1RfCpsG4hK8vMei/yW3yxLeqNst9vlmpqaeGNDQ3hGc7OnY0ZL35yWpg866mr+NMOq/1VtzH+pKzryldqo/9BaKXBQh33kqI4Fn3zrLx913fnGxyv+9uf3lvUt6e4OTeTwS9eWfkV19WENB9rrmx81h73/JwJeu4S/9DA5Hev0QnLXhmSj5aX46PCRjS8OXTjjlYFlYCbnlE4RA9PXQ012x71yJLRAStBrB5XJjCqO5HCHhcH8iCkQ+eqOr27qKiRlAJNMlqHTnLHgiZJOr882LRQfanRwocFifRoL4kWSwbjNI6WykGSduzaQiIZ/bogGTm14adOGbNOfKhyVtbRExGYtEaHal4c8M1/1DLUs8Q7Qvv3F0eGa14ZH6/844G15vi8wa0l3aKenOiP0BKF0jUgkBT49KSU45+4dNePzl0QsOmHWif61Rp3B8gu92fpkwj+6vSTLMD1DcKszKlvsb4Q9oyfO2tNz4w6vewczhOJTTIAJlCGBzI2+DA1hlZmAUgQ4HSbABJgAE2ACTIAJaJhAgpyCXV1dng9X9q3t6ln/bldf3x+61q6/01XffJHO4j3VEE+cEA97j437Rk+Ie0dOkYKec4zx8NU2nfQTu1n/kMNseNZls75W5bD/u8rl/Lja5eyudrvXVVe5+2uqq4awH2mtdgYXV8cTX7SHhB6eiUJ4xk3WhNfZtGbY0fjq7q31L31pZu1zC+qrfj3LZrytRYS/VxPzn1zr33SwWwQONNUMH73vfzvPeaNz5d1vfLj6T3/6YPknL7/Xtempt3qDT2X4Su9Uem08sd7ROLPlu/Yq94NicN1npXg8aydTxrTNNllX37oiHvR/NzhkPKH5Vc874hoh9R1Rv8+6I+p2//BEYRIl/tt0cF2zbLI+JEniOBHyF2Zvmi3k/NO560Zi0ej1EZ18ZvPLGzalXc7rcMNix74Gg/EqpG3OOYFISCdiEYOEv/FxZZ1e1lU3DiVCoSsbmzZdQs648WHU8rn3UGtrxCR+Jsejx8OeSVqbLKR4VC9i0cw2A6Korh+KhQM3xQK+k9pfGf6HdI1IqMVO1oMJFItAJaXLDsBKKm22lQkwASbABJgAE2ACTIAJZCYgb/762ybf+ytXbvxk1brlnd29b3f29L78Ufe633y0as2tJ3StvKS2ccYZ1YHwKXNNgRMbo56jG6K+o5pCg0d8Tjd4xGcTmw5fGB84fHex6aQT6mNvHF0dES69nDm3LM/KOoMsW5zLqgJDx5/mMxx1jid27Jej/zxp/j5//fbjS/55xVNvLv3F799695Un/vPxh0/9/b1NL7/cFb5GKOO06IYjLJGw3mE2Gm6RB9fPgJ9IylLtbYNJOiG5a8PCYv1daLD/8OY/bvoNPdlFAde/13C0sbb59+bW7d6oc+zyZN8xLQetOHRu7g4tSqxA6T/Yvqtc7XxGjkaOkf1exZx/wmBK6GqaP4qNDJ7W/Jn+m2c+1RssUFWx4SD3LJ3N8XM5Gm4sqGyE2FoVo0nWVzd0x0cGv9v4x/6fSveJiR+r2zrmtH6SrxG69QfYPmc02B+VdNJXRDSS8eu8WSllssj6pvaPZZ//tJbAyA30JGJW8TgQE2ACZUVAV1basrJMgAkwASbABJgAE2ACTEBxApxgFgTgbxCJJUuWxOirs08tXTX6Umf3hpc/7Fr5fOeaj+75d/d/f/HumqW/eLf7X3vUxD45zB3rqI/783eYQSEZf5Ld5Yn6R36ww58Gln526dIoyX5LROyaa0QCicsIVpRt3VF1861VzoekSPBU2TtsK8TBJEs6WVc/oy/h85zl8RhOm/nCphXpSkv26oMSIxtq490fusXG3qOMVTVPO+2RW1Z/s8OSHq6YxwApbTjMeYRU2/KM7PN8QYQCyq0Tzba4zlH1vBjqP7blT97npGtEwU+VDR1Y7dY7HPfJQe8OhZTNNkyhq7C7/xLd1H9i88tDT0tCyNuEUcEJeaEwbnjTcZzOXf2onIh/UcSieTtrpar6sGRzPBkd2HBC0/P9f5DQvlRgIqvABJhAEQgo17EXQTlOkgkwASbABJjAtBHgjJgAE2ACTKBgAmtPbLUutko3Wkf7tt/smJHyThNOiUQsHHik3Tb4h7wTyTEivD3ShkNrvmCwOh6XPYMHimgo76eq4L+UhdEU19U1/ykxMnBo04tDD273clc4XSX5O8JocLo/K4c3PxAnxaNSYkOPE/uzzMPB49LDFutYXiQMG46sPVNX3fxQYnjjbBENS0JSJjdiILlr3hGR0TMbXvUU9L6/lEbk/Iq6nXclwoH9JJ1OkfWsDEefZHf7JZP5ZyLg+2rLn0aXpvJT2371oqqqDXWOy3TOqnvloG+uFI/lVVpJx3Rj+/pEOHSJiPu+1fLi4Mdqs5X1YQJMQFkCinSYyqrEqTGB0hHgnJkAE2ACTIAJMAEmwATyIwAniiQFAmcY5cgJUiKR9xNJlDu9gy2hM34c9geupx8/oHPFFvkaoes/2HmUzlX1mDw6uOuEP5KQhSLk+NI5a0ZFLHFubMRzXOMfN76fKdr6/uYviFh0Hjw42NJCBH0mg6v2+o2H1TelnVX8cMNBjfZNVY23G+zu2xOD62skOS4p6fzT183oTQxs/F79swPrlVBeFkLa1DHjaiFiXxXxWEF1TIz9JcvKVbtRjkXOql+15mKUVf/YJVXtyPYNBzl2srvND+vtritl/yjKKyHlpaTNldA3tL+ZGB46sXHXvp81PLXJl1c6HIkJlDmBSlOfHYCVVuJsLxNgAkyACTABJsAEmAATKAKB9YdU7200mi4T4aCl0OTpKhk2AAAQAElEQVQlk9WX8I5cMft1/7Q4Y+QThb5/af2pupqG+xKegQ54VbDlZwU5lPR1rZ2xSGBx48vDv6Bfcs2UEuVpcLrPT2zqdWa6nhjp70i43F/JdE2Jc+Rc1DdU/UEYzGfHhzdaYPDmTQEPYJJBY/u62ND6M5peGnxbCX0pjf4j6r6FunGh7Pca6XOhIsuyLFXV9yS8w//P3n3AN3LW+R9/nhn1YstFxd7dJJtQAgSOfnf8OVhIsi3ZEEIWAoEc7ZIDwtHb0ZZ+cMABR68hEAjZJNtLNgksLbTk6KGlbrEtybaKZfWZ5z/jZMOu102ybEvyZ16SJc085fe8Z5Ndf18j6UWRHYlvyTua8/P+7l1zmmdwXceLZbBruzKN81R+zP3gyaqZQHZHc0KpT6jk4MXRnQM/lVvm/5bsmougAwIILIkAAeCSsDMpAggggAACCCCAQHMJUM18BIbWBiK63/dpVRyPPPDW33mM5nKbyixd2/+0sT3zGGXOXe0gLl6O/ofmC3zCTCfDUik5586TG+rOqhbuv75USKzv3zl8++TDx78eKofXyUppvSgXp5xPVsqa7vOfp9YIx/H9GvE8fuGqM2Q48gMzl3m2yqUc8z5nk4rS+1fHVWrg8r592X2TDtX9cmi9/2y9O/IxI518MKyse6iJjhMhZXfkt2J8/MLYvsz3rZOgJg402Y8jG7wrvYHxz+n+wOdVPmO/Rbuu3+GVw6m08Kq7jFz6kkh44J2RvcmhJlsq5SCAwAIL1PU/jwWuieERQAABBBBYGgFmRQABBBCoWcAO0GRnz8dVIfcPVohi3Woe4qEOVgKjhOa4VxTL75JbFv7KJGWFa/F819s0t+f9Znako97wzw6TRKBrTOn6m8ePlC9beePokYcWNcUT+623uj+0xciO+KY4PLFrYkylhsWaxjokNwYfofm9t5rDg48UxeO+qEUp0YhNC6/MmJnRt4Z3pfY3Yjx7jPiGyBmO2OnfMIYHOqWq822v9kAP3pUyTa0zckslM3phdHfitw/ubqqHP2wWrvj5Pc91+ntukrr8V5EfC0hVZzjt7zD07uhelUpu7Ns5vKdZv9m4qU4AxSDQhgIEgG14UlnS/ATojQACCCCAAAIIIDB3gaFS+MUil5735/7ZM0qplUyj/J+xA7mE/Xoh7/aXScSDPVs0b+BdZjblrzdcUdamR1YelsWxjdGdyc+sPnhfcba6pSN/hcoMP2HGOaUwzdzY1xoZhNrhnwqEv2+k4qcIoyKP1Sk9fiEDncde1v2o9fSljLHMmyKPO3qNNXhDEsXkBb1BfcWKbdXhgZXSNKxh6y5voqN1uqqav+MbpczwJf170/dP7GyyH4fP71nRU+n7qpDyajU+9ihRKtT1eYfWCVAi2JVWRvUN6tDwJZFJ30DdZMumHASWRGA5TUoAuJzONmtFAAEEEEAAAQQQQKCBAgNrg2dKw/ioMo15f+6fFcwo4XLvjj1p9PoGljjlUPbVVYlToh/VnK43m7m0R4r6rqxSQio9suoOlRg4O7I79RMrnVJTTnjczsG1gYuFp+N9qlqZMdTRuqJ3l7MjDfs22sHndD/a9HT8yMwO90vzuKvoHE6hlCFUfuy4Kmt7ap872Rk+aqQSL4s+fuAbcktjrlq0ry5VHeHvGcmBs2S1LGuraorWul6Sbt8HS7r+2lU3ZUcfbNE0DxOhtH3VnzIPynLhRaJSCkh13LmqoVL7i3S0zt7fiFLxmdHt8c9GDvJFHzXw0RSBthQgAGzL08qiEEAAAQQQQAABBOYuQMt6BOwvJtA6ur4tjGqkEZ8hp4zqkJY3XycbFB5Nt6a/bRDucCX2KWGo16h8ru4vU1BOt6GFV+6uDh/eGLkpe9d08x3bb4dZ8fN732iFjler8Yx/JjM7UFO5sa80KqQaXNf9GK1q/tCaN3J82KmkVDLQZWqhqLJSwGOl1vRoj6F1R/8qK4ULYnvTOxp1/pQQMlGOfUQVc2tFKS/FPDfl8uaU1K6MZBMfWrX1SGGewzW8e/KC3v74ivC3rXP0bVGtnCFNY8aAeLoCJv7smGZBur3/M15KPTu6M/E7C8/inK4H+xFAYLkIEAAulzPNOhFAAAEEZhbgKAIIIIBATQL+XvMjsjT++OMDpZoGOK6xlU5UrcBiS/jm4YHjdjf8qR1adnpXfk5Uq68U5YKrrgmk9StUKFzWuyJflpnEpX37csnZxkmsCQeScvW1qlL8qKqWPTOFf/ZY0hvImZXy9fbz+d6PbvA9QXPJHwnD6D1+XvsKMen0/E56fL8yhu6rbxpNF1pP331GPnVR+MbB/6tvkKl7Jc7rulQLhl6nxlLa1C3muFfThOzoHpbl8mXR3aNfkwdFdY49F6WZ/XmQg+f3XGmUij8XxfzF1n8HvuPPUy1FKN1pWufzr8LheVbkHwbfsnp7Ol1Lf9oigEB7C2jtvTxWh0BtArRGAAEEEEAAAQQQmF0g8bzYRiGMy1W5NO/fJ6zwT0mP/0fR8dTXZ5+5/haHN6/0+nrNL6py4V9FteSsZyQ7NNN6Y2lZKb4pXrjz9eGdw7O+bzZ5bm+/XNH1czOTfJ4V7MzpG3el031LrJie8YtE5lL/4LqOpzo8oVtVudx1fHul66aslPeLXOJ5wqicefyxOT+XUmi9K4rGSOI1fTtG7xQN3Aae0/cvWmTVZ83hoy5pbfUOrXSH0nr6D8lK6eLo/vQ2KYT1x63e0Rrbz367r3V+zpMe86eyUvyENfpKqVlppfWk1psSUgmvvyx9wWsMs/D02N6RX8gtjXkbdq210B6BVhJYbrXO+y/s5QbGehFAAAEEEEAAAQQQWM4CQ8+NRqQ39AVzPOe1shk5Xwvp8o6YpfJr5QJemXV4s/C6TPkFVRi7VFTLjrpq1p1K711x1Arynh++YeBzZ20V5dnGiZ/X/TTV5f+tMTzw6DlbOV2GMTbymfl6DGwMPEPvCB2w1hySUshjtSqpKeny/CEZyl9olMXjjMSRDmltx47P7VEKGQgpM5f9ZiyQOjC3PnNrlbgg/ARXV+8NxsA9HXPrMU0rh0vp3f33WgHl88Pbhn44TatF3622CC1+tv9x8b7ebZourlOlwuOk1FzWKZB1FWOtU+voiYuq+cqkuP+V/bvGhusah04IIND2AgSAbX+KWSACCCCAAAIIIIDA9AIcqUXA/hw7zRP8pplLr6r3ywlOmM/tNZSUH+7b19gryI6fw36LpdNY9TlVylvhX6Wuz1UTE+Ff32EznbwkunP0ZiupUcfPMfm5fXXX0AXh10ldv8XMjPTUEu7IQPfvXNI1r7fTDq3zn+PojOwyx1IdVq3W7cEKpRRaKJw1i+Icd+5h0nnqw78s6tncXjtE3GtUjDfKrcKoZ4ip+gycE3iU7I7trg7ee8LbladqO9M+K+CceGtyNXH4hfbVcDO1Xaxj1h8YOXCO55TEL0OfEV73D0SluEEZ5rxCdOkPmdafl18YY5mzo7sS35pLKL1Y62UeBBBoPgECwOY7J1SEAAIIILDYAsyHAAIIIDAngXi++7VCqbNVITen9jM1UvaVaN7gT4Zd8c/N1G4+x+69MBTSuoPXimLuMlEp6dLaah7PCv+0nuhhMz5waXRn4qez9T+8rqM7ccap3xDV6idUuVhbwOMNmCqT+FDPvtHsbPNMd3zoXP/ZWk//DWYqGbSSP+v295bSG6io0aFX9O0bSnboox8whgdrCicnRtIdQvo6fu4YT7y4f9dAfmJfA34Mn+9d4Vx56nZj8N4+6zSdUHctw0uXV2jhFfdXRwYv6bsp+8ta+i5U25EN3R2D63xX6B09P1Rm9d9VpdxlrVGz7nWt0/5vR+vtLwtd+6azmNywkAH6QpkwLgIILL4AAeDimzNjkwpQFgIIIIAAAggggMD0AoMbuh+tR1e9x0wn5vQ5dtOP9MARLRROmqPxKxfqqiX7yr+At2ermR7aKIxKXWHLxGf+9cQOmYkjl0T2p37yQOXT/0ycH364N3bKzSKffaE0qzVebaiEFur9vcvVdev0M8x8ZHBd6FladOX15ujQSeGf0jRllMa/Gr0pd8PAOa5HSW/wDfUEULKz+y9y+OjFXQ38gomUFdSaXad8y4gffng9NR1TmQj/oqsOVVJDm/t2jyx5+JdYEw4MrQs+x+jo3Ku7fJ9S45lTpZD1BdHHFun2Kr3/9Liolq8Mh3qvaOR5ODYFjwgsB4HluEYCwOV41lkzAggggAACCCCAAAI1CPxh86Ndjuiqb5jDAyEphHUT89pkoMtQmZH3xA7k/jCvgabpbL8FVw95v21kRp4thawv/JOacvSuuN/IxF8Q3T/2s2mmemj30IaOf9IiK242RgafIKrVmn/PskyUyo58pmv7fXV9c+vgusCzHJG+G8zhwU4pTjxHytqkv/MPsadkrrx3jfA4Vz5ijxobdYgaN613ZU6NDj0/fHN+oMau0zZPXtAbrHi7rzZTI8+USlmlT9t0xgPS5RFadNXRytB9F/fdOPSrGRs/cHDBftrh88C6wPNENLBLC3Rea4Xm/6zKxfo/5+/BSmVnr6n19P9SpIbOC19771fkl++oPHiIBwQQQGBWgZr/Ypp1RBoggAACCCCAAAIIINASAhQ5V4Gomf2gKow9SZSLdQc0x+ZSukNJr+/myMrU14/ta+Sj2iK0xKmrvmqMZy+QplHX7ztWXqb0yKq7zPTRi2I7Rn4xU31KCDl4ftdGx4pH7DaTh08V1XJdRjIU/qs5lt0t6tiGnu15tiOy6npzeKBLihNDNHstMhBKidGRc+UWYfqDPe82EkdOq3kal0eZuZG3RveP/67mvtN0GNjU7xPejqvV+Nj5slqq61xNDG1/EUbstIHywF3P6ds1smTh371rTvPE13U+T+vx73UEQ9eY6eQz1XjGI6VWVwg9sTbrh9J0pcVWl4TDc1U+fu+G8I2D8/qMSGtIbgggsAwF6v+f7DLEYskIIIAAAm0owJIQQAABBGYUGFwfWCO7Iq9VmWSNb2k9eVg7jNLDqwar2fSb5JdF5eQW89ujhJDJ36/8jKgWLpVGpa7fdawxrPBv5e8qg/dfENmZ/rWYYVN22Hhh5FJHdPW1xtA9PapSnqH19IesgE6ZiSNfiR3IJaZvNfUR+ws/9P7TvmeFf91CmSc0sr21YFdRjWdfE711PJ7c2PsI6Q+9xQpG5QkNZ3kxMU4o8lvDcHxzlqZzPvyHzcKlexyfM4vjz5lPsKw0h/2W2EE1cGRT/57MHXMuoIEN7bUMrPOv9/eaO2Rn1zXmaOJfVC7jltY2n2lsd+Hxm/qqR9wn8mOvDft9/37qnkxqPmPSFwEElq9AXX8pLl8uVt6uAqwLAQQQQAABBBBA4GSB+8/r7HKe+uivVgfvdZ98tPY9WnfUMIaPbOnbMXRn7b1n75G8oOf9qlr9d1Eu1RVW2lcn6tFTbytnE5v6D4z9eaYZ1eXCmfx1+FV6KPZliJof2gAAEABJREFUc+ieoKjWn2fqkZWjqpi9cab5Jh9TQsih8zrP0cMrv2OODvYeH/7ZwZHSdVPvW32fyqZfFNufvfbeNad5VHfPDWY64RS1bi63MlJDb2rUl36oNcIREae8T5rmZaKUrymMPL50JaVy9J12xBi6f0N49+JfFWeb2gFsr7Fim6MreqM5NnqOyo7MO/iz12ivTe+KFoQ38F1x+J714et4y6/twh2BRggs1zEIAJfrmWfdCCCAAAIIIIAAAgjMIKC2CM3b0fspY3hgtTSqdYc0D03hcClrkN1jxujVD+1r4JOhtYEXCHfgbaI0Xlf4J3SHqcVOu8XIJi9asWP08Eyl/W2DcCcGe98hgz2fNOL3e4VhzNR8xmP22zvNVHJftLN4aMaGkw7G1/ufrXVGrjFHhsLC/PuVf3b4p4fCeU3I/xKjg/8QPTC23e7q7xh7gzXPY+zntd6tIOpvupS/qrXfVO2V9ecq2Rl+tdAcb1HjGW2qNnPZN7HO2Kl/Lh2969zozkStb0ueyxRTtlFCyNFzujqH1vtf6O+p7tW7+3aKfHaDSie9Usq61yMe3Ox1CV03rGD3L6o0/upsWrwivHf4rw8e5gEBBBCoW2De/4Oqe2Y6IoAAAggggAACCCCwZAJMPJtA8s7VF0u391IxnrZ+Z7Bij9k6zHDcDjW03hWHKyOJdzx8nyjN0LSuQ8Pne1fosVO+bNbxxRb2hErTTCtw+YF5eODFsW3xGd+Ga39uXYen/4PS3/Euc2TAdfyVd/ZYtd71yCrTyI1dL7eKOaeIgxsCz9Q6o9eoVCJy/PwTzl3hUTMz/JLIntQ7wzuHx+x6khuDjxDe4HulUbEyWHtPDXdvwLDW+Z/Hxqqh50lN1RrhiN/e/Qrl6/zv+bylXClh6tHVP1DxoWev2D/2l5MmWoAdyg4u1/X2DW3senO1p+tHsjN8lZnPrjFTca+Fat3mP6l9/qz/5sZloOcbZnb4/PC2oasfvu+uhv/3Mv9KGQEBBFpRwPrLvBXLpmYEEEAAAQQaIMAQCCCAAAJTChy9pH+VFu7/tJk4ogthZxv2XdS96T19hho++rHZ3lZbzwR/2/Awt+o+fbcxMhi0qrRutY2ihFCO2Ok/Lw3c9bLYgZnDv5EN3R26S35cOp1vUOmkU1hJVG2zTW6thNbRPaQ7Pb+cfGS610Mbuv9JD/Z8V2VHouK4z/yzwyOtsyej0kOXR/ePbTvW//Bm4VWd0WtVZqTmt3FPjBkI7kv60nV9OcmxGuxHOzhNdvd/Ug+FPydScZe9r6677qhaYe13RHrwwsje5FBdY9TQyfrzIZMX9fYlfx19v+gO/Uz3+T9ihXOPFZkRl1T2Ra01DDZNU9tZmYahBbr/rIT6t4R592ui2xN3W3+Yremn6cRuBBBAoEYBAsAawWjefgKsCAEEEEAAAQQQQODvAupJwunS/P9lHP5rVJhzvijt7wNMemZ/rp5y+35gdjmumnRo3i/tL1/o7FRXm7nRx9XzNmU7eHGe9qjDRirx6hUHCjO+7df+PMSq2/0FocQVKjtqBaPzLl8oqSs1lvphr37/jFcdHpspvr7rcdLr+Z6ZTcWOD//s45q/s2COjb41ur9wwmcJugo9m1Uh93i7Ta13x8pHpM2x9OvP2irq+3aTBydMXRgKOXzuq4TueI05PFj7ZxA+OI5w+0rS7ftYeuRPr2zEFYnHhp3u0Urf5ODG0L8Ib/et1p/jd5jp5Kkql9EbFfzZ89p/BqXUxmVn79eqheyG2M7ha+frbY/LHQEEphZYznsJAJfz2WftCCCAAAIIIIAAAghMEhjq636OMo3ni3JRTjpU10tHbPWozCb+M/at+HhdA0zTyQ4qI+qUL1sh5WZRGK/r9xo9csq4mU2/Lbrt6G+nmWZi99DaQMTrCV4tlHGJyGfrmmtioEk/tN4+ZWZHDszl7b9H1wcfKTzuG1Uht0pa0eEJQ7k8FVEq/k9039hXj98/uCEW1rpjn6jnizaswMs0cpmPRLYn7jl+zFqfJ9f19pVdoRtUpfg8NTaPt5P7O3JCqjeE9aPvmefbyOe8hCObV3a5oqd+xEgnz5w478ddcTnnQaZpaAd/yqhWhMvzF+Fw/Xs2d+g/+vem75+mObsRQACBeQs07C+veVfCAAgggAACCCCAAAIILIoAk0wnYIc1zuhpn1aZYcd0bWrZLzt6zOrw4Fd6bxi8o5Z+s7VV9uexrV71MaE7LlNjo1YepmbrcvLxzt6yKuU/3PuIv1538sG/7xk+v2eF1tn1PavteaJUaOjvT3pnJF0t5X/w99mmfhbf0HmGw+XdpYr50+Wkt50qzWEITb82PDTyPiuxfQhCWUa6T3+dFTD2CKWmHniGvY5VjzhqmuNXHT/mDM2nPDSwqedMs9O/TxXGniXyOcuu9jrsoEx29Y6oaulF0e2JL8wlLJ2ymDp2amOZsHR7H1XP1aXTTWevRxlGVfMH75PBrndphnxWdPfwNYsVak5XF/sRQKD9Baz/Cbf/IlkhAggggAACJwmwAwEEEEDgBAG1RjhEd+h91aN39dUTGJ0wmPVCSamky/Nb08x9Yj4hkjXUSbf4n07bJIPdV6pMwgr/zJOOz7rD7TGkob4Yvvu+j8stYtoBBteHTjNdnm1mLvNMUa3jCzRmKERpujJSQ7f05VIDMzQTAxtDp1rH96hy4WHWYqX1/KGbEkJpXv+Pi5XC6+QdovLQAetJ/I7OU4U3+HpRyp/Qxzo0682++q+aOPTJvq1DyVkbT9HAqktadmus0HKvyqYeJyvlmmuwh7XDMr13xRGVGt4U2zG8y963mHfdVE4lRUPe7i2szV6PFSimtc6ez1bLxTWRGwf+ezE+x9CamhsCCCAgCAD5Q7CsBVg8AggggAACCCCAwAMC8WDPM8x89qWTQ6YHjtb+0xE7rWimEu/o3zU2XHvv6XskL1jV7+iKfsEcvLuuqxStcEtp/s5d+nj23ZNDs+NnHd7Uc6bm9ew2xzNPnnzV3fHt6n2u9602jOGhq+VBUZ1uDCtEO00zqvuVaTxCCmHdxEObElJpwdDvVH7slafuyaQeOmA9UVaYq7uC71O5tK+eMNd5ypkZfSwz45WR1jRT3uy5E+v8L9G93q1mLn1avX+elBUg67FT/yyzw2uj+8d+NuVkC7zT0GRFGMa056eW6ZUQht7Re6fSnS8LDx9+S//u1CHrhFq7axmFtgggMB+B5d6XAHC5/wlg/QgggAACCCCAAALLXsD+jDvNH/qSKBXq/4KG4xX9ncosF6+LjI/eevzu+T63v/RD+V2fNQbvjYmJPMyKUCYexZw2+6o7Z+y03+mV/Ot69o1mp+uUPL/3iYam71b53KOltU3Xru79UgqpO+/zlLSfTjfGRPinCfuqt0dKazu+nb0OvTvyF6NYuTS6L3P38cfs50lf6CwR6NwsiuPSfl3LXUlNGWOpXb2hfLyWfnbb0XO6OhPe0LulJ/AFVcj1WmXXPL89jnB5lNZ/+s+1sfTa3l0jf57Y15gfNY3iUkZWalq6pk6TGtuewu3LSV/wWjGeuyi2bWCHnCH0ndSdlwgggEDDBLSGjcRACCCAAAIIIIAAAgg0vQAFThawv0xDBrreb6biZ0w+Vtdr3SH0zt5DjnL1PY0MOpQQMjze8QIrXLrACiqlqHXTdKH3n540ho+8tnvrwKGputtzJDaGnmFqcsfE5+1ZCdZU7ea9z+NX5tD9u0MH05mpxrLDP+nQtitlPsYq4YS12oGS3hUbEJn0i/t2Df1xcn87JJXBri0qM+KZfGwurx19p1eNkSNfl1uFMZf2dhvbzQqRz6r4ndcpqd6pSnmfvb/mu7SW6uswrfXdWh69e1PPjUeP1DxGAzsYKphVxfG/WeuzbrUNrDRdCY+/ogVDf5aaeJ1REZeH9w7/tbZRaI0AAgg0TkBr3FCMhAACCCCAQIsIUCYCCCCAwEMCib7uZ4lK4aXS2h7aWe8TKYXWHauYqaH3dm+9Z8qQTdS5DW4MneI45awPmYP31P6ZbHZd4f6KMXjvu8M7kj+ZqgS1RWjD53VtVE7v91SpsEJa21TtJu9TQijpD5nSH8wr3WG9nNzi5Nd6V7Rq5rPfs+Kuk9pPhH9O5/XCNB8nlbKaHNffSpK0rkjeSMXfEt4zPOUXq0TEikfLYGitKuasjicNb+2b4aZZvx66PL9Xjo5fzdDqhEODGwLh+NrAldLv32+FpudK06z9/NgjOlxK6+kbF/7gdx3Dmeev3DY2Yu9eynv0afGCkRm+TXoD035O5PH1WdpKeANV0dE9qvs7f6l05/vMauH88Lb4N/p3DeSPb8tzBBBAYLEFrP/DL/aUzIdAcwhQBQIIIIAAAgggsNwF4hdEolZ49XlRKbkbYuF0K1Ut31xyOOr6/LjpavjD5ke7nD19H6gO3bNyujYz7vcGlRVO3VAY77zaStSsnObE1mqz0OO3hzcrt+8bqpSLWtmf1ezENlO9sgZSWlc4b1bKHzXHUi/XOntnD4qkFEoZh3Wf/4+Txxxa27la97ivE2b1idI0TqzB6qd19pRULvXJ6FNT35vc135tr0M4A/9hjCbquvpPhsKmkTz01bmEVUfODvbEN3Rdqvu79glNflLlx/ultdl11Ha3lunxmXpk5Z/MUvElkcN/fmloz6ETPtOwtvEa11puEaYsFXda53VISDnlwOrBTXh8Y9Lh/qX15/+jolTYpOXG1kYfe/gjsRsT91g91ZSd2YkAAosmwERCEADypwABBBBAAAEEEEAAgWUoMPFWUbf7Q2Y6cXpDlm9fndbRnTFH4m9btfVIoSFjPjhIbzW9QTpdL6jnM+2EwyW07ug9Rkm8Y/XB+4oPDvnQg/0W6Hih92Wax/sFM5/tlUJYNzGnTe+OFcRY5m3RnfF36lL+SDqcswc9DpcyUomDvU8cHj9+ksS6jofJgO9as1J5sjSqJ9ZghU+ys7dolksfS7ojH5RbxJRBY7IUXq35AxeKfFaKiWVYD2KOm+4Qjr7T73UFw9+drcfQeZHTXX0rviNczq9bwecTrVkc0tpm6zf5uBJSyY7uiubr2G8ODzwnuvX+bXLhPh9v8vRzep3w999ppke+Iv2d+QezvmMPptCdeel0H1KVym41lrq8qCobortG3h3dkbjN/oxJuWXq8zSniWmEAAIINFiAALDBoAyHAAIIIIAAAggg0KwC1HW8QLgQOk8Vx19i5Tby+P11P3e5lZHLfiF6IPfHuseYouPApv5eR3jFB42Be1xTHJ55l5RC6+krm8ODb+vbft99YtJmh6Dx/u4rNa//E2YuFbIgrNukRtO8lF2RimlUvxh+8sgXrE7KUNqjVLUy69tftY5uU5TK2+Vx4VDigvDDREf3d0S5/BRpVORJU3oDZVUpfiTSe/QDZ229s3zScWuH2iI0UzpeYWZGQtbLmm9aeKVpJo9+suuq36Rn6qyEkHpv+G1m4uXpNa0AABAASURBVPDZolRwSWsTdWxK05VjxemjQmif1FIjL4zsTN5VxzAL3sX21szyJ41C7v3SG7zVCvxuVabaqkrF/6nmRq9U2bFNyl+6JHZr+dpT92RSUgiLaMHLYgIEEECgZgECwJrJ6IAAAggg0NICFI8AAgggII6u9a6Sga5PiEql9lBtKj8phRYI3auc6n8aGYBYSYrURPklZirxaFHPZr/1N5f5VuTw4M7J3Q9vXukNG7G36b7O96tcqsOq27pNbjX1a9nZa1hh0Dblyr9HbnngKi/pC6wR5ZMuMJw0gBLS5UlXPb7bjh0YvDB2mnD7rlbF7JNEtSyP7T/2qHSHIaqVr+RHBj4mvywqx/ZPfhz8ZWiVozf2IjU2etIYk9tOfm2FWkLzBn9fTqWumnzspNebhab5O58gpLR+l1QnHZ5th335nPAFK3p41U/MkeGXhNU977Svlput31IeD+8cHov5Uh83DPkcVXZcOBIaf0lsTfWtK34ovhH7Ufn3/bsEn++3lCeIuRFAYE4C1v+059SORgi0lQCLQQABBBBAAAEElqvA3zYIt8vf/WFzLH1awwxcXtMo5t7Vt3Uo2bAxrYEGz/GscvSd9mYr1Kr99xaH0wolO+6SuZF3yztODM6SF/QGXUp9QHN632FmhgNCzT3IksEuQ3r8eyvJxGti34pPvI3X/uw96Q08VVRKM4dvDpdQmdEfr3AcTVvLEyMbulfqmv4tVcz9oxXGnrRGqyplhXM7XFrlXasPimnTRaud1J2ufzXzYytrWYtdg32XvStM49CfPzOXz/4Tj7FmMIyjdr9a71b4Z+q9/YNWvw8b2cTFke1H9tXybcNWvyW72XXaPrED8fGztoqy3PJA8LtkBTExAgjMWYCGDwic9JfMA7v5iQACCCCAAAIIIIAAAu0o0Onofo5y6pulYchGrU/z+X9ilsWORo1nj6M2C93ZE73SiB/us1/XdpdCBrsrRnLoTeGb8nbg9FD31IWhkOnw/reVmF1pZoa9Dx2YwxMZ6DSk23OTSsYvt8Kg4WNdBov9bs3X8WhVLh3bNeWj9HcYxujQ5+wwKbHGH6u6HNeoSvFpolI+6fcyJTWlBYI3qXzuVV3b0+kpB3xw5+iG7hWO/tWXqszwSeM82GT6B/vqTW9gUIzrc/riFmkFXyo9dL2QoiysH2IOmxX8KaHrJSs83S2yoy+K6AMfiG2LJ+bQtVFNGAcBBBBY9gK1/wWx7MkAQAABBBBAAAEEEGg9ASq2BYbP71khgj0fNHNZl2jQR5VJf2dRjI68wQrEGvo2yFFxyiOFr/NyURqXNdfqdCppVq4dDqRustd97D64IRAuu0Kflw795Soz4j62fy6PMhCqSJf7ejObfnlkb3JoUh+fcDi6hTIn7T7xpXS6xzRR/fnIBtFhBr3XKqP69CnDPysx0wKhPaqceWnsQG7WoMxwu85R5fLpwjROnHAur3wdyhgZ2B8+mJy4mnEuXSr5yk16V+y3VplqtvZ2G6k70qZh/lcllX55eNfwD+0AdLZ+HEcAAQQQaKwAAWBjPRkNAQQQQKCZBagNAQQQWMYC6nLhNFye96jC2BnSbMzVf3a4YxrVr/T+c+Y3jaRVa4TDdPv+w4gf6hATV5lZGaCY42Zf0RYKH7XW+A77rZrHeg1vXrnC0bPia9b+zSo74jy2f9ZHazwZDBWsCj6tV0aviO5MxCf3cZRzIaGUFapOPjLptWk4rYhufVV27BZm9V9ktTLl72NaoOvP1VT8NdGd4yfNNWlEobYITQuFn2ikk7N+AcnkvvZrvTtqysLYNmt9yn49l7sV9g6b6ZE3WvP+zf4zcHwf+/VDd8OoSofzT6JS+Y+czH5k5a1jI8e35TkCCCCAwOIJTPkXzuJNz0wILL4AMyKAAAIIIIAAAstRIH6o8xnS3/EiUcw37HcArbsvqZXHPiy3NPbz0OL+zlOsWOuFVlgnaz1XSnMYZj7z+t6tRx76nLr4hZEzlDvwbTM/tlHl0o6axvQGcqKQf3tcHn5n99ZUZsq+utYvjOqsruZYyqf3rPyWEtrTpWFM3d7fmRW50Tf231I8NOVck3duEcoo5G/XAp25mq+UtMbSPIFi0e35mfW0pltkX+o2lRp+mfQGf2aWi2OqUs4KTb9POt0/U073ASXENiXUu4xK9XmR/zf+nYfvEzO/P7qm2WmMAAIIzE2AVn8XmPovnb8f5xkCCCCAAAIIIIAAAgi0uID9uXd6qOdDKhX3ixq+8GKmZSupKTU2+pbI3vHJb4edqdusx6zgSEq391JjZDAwa+MpGmhe383Z/ODuY4eGN/WcqQV7rrPCt2eI/Jh+bP+cHp3usqyUPhj2jnzu+KsJJ/dVblevUmr2360qZanGM55pg02XpyJLhXeHO8ZvnjzHdK+thFTl48Z1Kp14nQyF71GaZk7XdvJ+q2alpCitFB1WeDj56Myv7XmjB8Zuq4wNXqCqlX+sitJTZD79z86iuVHPaRdXO3IvjoUKH+8/MPZnuaWxAfHMlZ10lB0IIIAAApbA7H9JWY24IYAAAggggAACCCDQugLLu3IlhCybjtcqJZ4sqhUpGrTpXZHbDem+vkHDPTRMcqM/qgV7XyXncEXdQ52OPXE487JcfP2xq80SF4SeoELRbWY6+QRRqu3KR6XrptC0r2eqI5+a7TPrlO6e/e2/x2qc5lFJac2nf3U8F/zybPNNHmL1wfuK4R3Jq4x06mzhdH1OON1pO9yb3G6q16qYHxHiTmOqY3PZt/JWMdL/I/En6/GvkYNiqPuWVCZyMJlbtVUUal3HXOajDQIIIIBAfQIEgPW50QsBBBBAoNUEqBcBBBBYpgKjm095lB5e9WqVHant6reZvDw+o5qNX9m/ayA/U7N6jpnStcYYPhKtta8deEm37xu97uG7lBAyvqnnQuUK7jNGBx8pKqWags+JsZyugy5ZfsexMFHMsGlSKCFrmuKE0ez5tGD3L1U+t8UO8044OMcX1uyqb3/6vmgm+UZRKTxTOlzbhVB5e+yphrD3a6HecnX4yOcJ6qYSYh8CCCDQXgIEgO11PlnNLAIcRgABBBBAAAEElpPA3zY8zG043R8wEkeiYpZvqK3FxQqXronlsv9XS5+5tB3Z0N3h6F3xn3K6z8ebYRDp76iqSvkD6UooOPyC0z8nlLpG5dIRaVStbGyGjpMO2cGY9Ph/rhVyL+7ank5POjzlS2WosjWfmvLgHHbq3bERMx1/fWwO3/g723DyoKhG94//LukbvcQsl54lq5Ufq2rlhM/fU9am+YMjxljqsqg++L+zjclxBBBAoBUFqPlEAQLAEz14hQACCCCAAAIIIIBA2wgEtZHnSodzo6gUZaMWJUPh8Up2+F3SCpoaNeaxcZRD9leThx917HUtj3ooojSH3FQqGL820vErrDDQK4WwbmLOm5WLKc3f+XOhcs8N35QfnGtHTVgBYL1XAPqCZSObfHv0aYVfzXW+ubSzP7Ow7/uVXya7S+eaprrALBd/b5mUlGlWhZS/M7PZZ/ftzVzX5lf/zYWKNggggMCyECAAXBanmUUigAACCCCAAALLVWD5rjt+tj+q9654hxE/5G6UgpKaUrnU+/sPFI40aszjxzE08VRhGHW9Vbk6eI/TGMt+QQh1qpSaJq3t+LFne640XWm9/T8Tevmi6M7x+Gztjz9ulitHrDmrx++b03NvwBDV8keiQ5mr5ZaF+aIMOwjs/37pQNGsPFWUSo82x8efNV4de2bslvHfz6lGGiGAAAIItIUAAWBbnEYWgQACCCAwowAHEUAAgWUmoISQMhB4rRpPP8bKwaRoyCaFI7b6sNMrvmQNaE3RkEFPGETqrlNO2FHDC6mUlLrDUdd6nW7l6I7+TI6Nbo5sTQ7VMO1EU4dRvVeYxoDQavj1yhMwhVn9Uj4b+C95h6hMDLSAP1YfFMXYT8Q9/T8VPznjFpFZwKkYGgEEEECgCQVq+BuqCaunJARqEKApAggggAACCCCwXATi53U9RguveLk5lm7cv/cDncocPvSW7q2phQuPqqW/CKEWJFyc9ty7fUrr6b9NpIefH945PDBtuxkOdO8bHTMSRz6qdUUrQlrx6AxthdSE9HdWhVH5qsyIt9X7pR8zTcExBBBAYLkLsP6TBbSTd7EHAQQQQAABBBBAAAEEWlVArREOvavvbdX4oZi0toasQ9eF5gvelqmO7mjIeNMMUjXkH6TbW5nmcON3ewNKhlf9vJwafmHv7pGj9U5gRX4qawx/wxzPfkULhUtiuisBNV3Iju6CWRx/b7Ya+o/IwWSu3jnpN6sADRBAAAEEjhPQjnvOUwQQQAABBBBAAAEE2khgeS4l0dX/LOF0XCgrJSuXaoyBFj7FUAP3vP3h+0SpMSNOPYrbVEdlz4pfC9Gw0sW0my+otJ7Yr6qjR1+4YsfA4WnbzfGAbRNJD73OyI6+VHgDP5fdsTHhdKtj3ZXuVDLYPWSODL4y+pTUfz18310LanlsXh4RQAABBBCwBQgAbQXuCCCAAALtK8DKEEAAgWUkkNgcDmhdkXcYg/f5G7Vs6fYJVRq/Ifz04m2NGnO6cXr2jWbVyKE3a5FVKSEXMAS0w79Q5I7qkXtf0H/j4P3T1VPrfnlQVGN7UtdGtKNPL6eTzxDK+LzW2fNHGYr8VWjymvJofEPsluJ35JaF+cKPWuulPQIIIIDA8hEgAFw+53pZr5TFI4AAAggggAACy0FA5UrPMQvZ/yeFko1Yr7I2fcUZ+erY6FvkIoVWkZ2p24zU0Mu02Ooh4fJYFTT4MwGt8E/v6fu1kR54Qd/+9H2NcJo8htwqjJV707+J7k5d2Ru/7/EJ4+7HRneNXLby5vHfTG7LawQQQACBxgow2tQCBIBTu7AXAQQQQAABBBBAAIGWEhjcEAhrkVPfIVJJV6MK1zq6VXVk8Iv9u1OHGjXmbONIIZQVlu00RofWasGunXq4v2yngLP1m9NxX4fSwyt/Y47GL4ndmLhnTn3m2UgeFNWztoqytNY1z6HoPncBWiKAAAIITBIgAJwEwksEEEAAAQQQQACBdhBYfmvQnf5XmpnEmY1auZJS6SvOSErT+95GjTnXceywLLYj/vv0qPkClU29Wo+deo9wecy59p+yna/D1Lsjd5hH731B5IYjf5uyDTsRQAABBBBoUwECwDY9sSwLAQQQQEAIAQICCCCwTASGNkVXy0DHq0QxrzdqyXp4hakShz4Y2XpnrlFj1jqO/UUZkZ3Jr6vU4fXS47tRBroqdV0N6PGbWrDrDvPw3S+M7E4S/tV6ImiPAAIIINDyAgSALX8KWcBsAhxHAAEEEEAAAQTaWUAJIXWv59VGJrlCNGpzuJQVtv21p3L3Fxo15HzGiezM3uUYzb5SqepVVpBXrWksp8eUvs5fGMOJSyI3Ze+qqS+NEUAAAQRaSoBipxcgAJzehiMIIIAAAggggAACCDS9QHIVghzuAAAQAElEQVRdxxmmWX2JNIyG/NvevsJO7zutbN77pzfZX2bRLADdt6QyjnzxzcKoflP4Omb9XEB7HcLhrEinvqcynnxBbM/ifOZfs3gt4zpYOgIIIIDAFAIN+UfCFOOyCwEEEEAAAQQQQACBJRJYPtPaV/8pf/AtajwXadSqtc5eoUr5GyP7UvsaNWajxunZN5oVjuobhFH5tBYIFSdCvikGn9hvmsOqnH+vrFYvXbFj9PAUzdiFAAIIIIDAshEgAFw2p5qFIoAAAstMgOUigAACy0Bg8LzAmaJcvFgqUzZiuUrTldYdO+rIFl7diPEWYozI1mQuW068WxWyH9W8gZNCQGWaphrL/cbM5y+KHih+NLxzeGwh6mBMBBBAAAEEWkmAALCVzha11ixABwQQQAABBBBAoF0F1BahOdyd71PVclej1qh3x8rmyMAburbfl27UmAsxzsP3iVLSn/2IkUt/TJjmiDINw77qT1XKJZUe36OkuLD/p+InVio6v28OXojiGRMBBBBAYEEEGHRmAQLAmX04igACCCCAAAIIIIBAUwoM3975eDM3ut4KuaxbA0r0BpSQ2oHw1kM3NGC0BR/irK2iHOsqfqBayW8y8/mrzHRun5kvvd3hFJda4d+hBS+ACZpRgJoQQAABBKYRIACcBobdCCCAAAIIIIAAAq0osDxqVmuEQzkDnxBSDzRixUpqSvP4D1WHD73CShNVI8ZcjDHsLylZeVD8vO8ccXnfKeKC/p+JT4V/KhbkLb8WilRbhKaeJJyHN6/0JjaHA/deGAod3tzRPbCpv3dwcyxs34eeG43EL4hE7bvVJmY/2vusPt3JC3qD6nLhVEJYzIINAQQQQACBRRMgAFw0aiZCAAEEEFg0ASZCAAEE2lwg7u5cY2YS/yitrRFLlW5vTmVHXtK3L5dsxHiLPYbcIkw7DKx3XiWEVJuFfu8a4RnZ0N1x5OxgT2JjOHb0Od2rEheEH3b0/M4nJjd2r0v8svPfEn3dH3JVja8p03m9T/PtdpY9BzRZvlkrVG+x77JUvlkYlVuEUb3VLBg/UFId1BzuW1wu/z4z2H1dYvS0j8YvWrUpsXnlw4bWRv2CDQEEEEAAgUUQIABcBGSmWBoBZkUAAQQQQAABBNpR4PA/C6/0+z8hnG5PQ9bn8lQ1Xft4ZE/qxw0Zr8kHUVuEZgd9A5uCvUNrO1cPrO180uC6wEVD2Y73enyha6oOba/D7fiBWRn/sZZO/9RIJn+qpTM3G7nR76nC+CdVMf96NZ59vsplzrUenyaK+SeKUvEfVKX42Im7UXmsqlYeo6rlR4tK+ZGiWHikmR09Sw0ffYpKHj1XpQZfrUaOXG3msvu1kPuDdh1NTkZ5CCCAQNMLUODsAgSAsxvRAgEEEEAAAQQQQACBphFw93RfYgVPj5LWNt+iJt7629nzo96evo/Md6xm7G9f1Xd4s/AOrQ1Ejq4PPnJofefZQ7d3vdnX1XeNLr3fV9XCbbKYuVUrF6+WRvnt0qhcqMrFpwmjcpbQtDM0r3+V1hmMaIFgt+YLdki31ycdTqfUNF1aP6xTMOVtwkJqSro8hub25K0+o1LX40LqA0LTj0q3JyFMs2RWqv2OsgxNtOfHfAXojwACCCAwgwAB4Aw4HEIAAQQQQAABBBBoJYH2r9V+e6pwB94ppHTMd7XK2qS/4y6ZSl4mv3xHZb7jLXV/O+wb2CR8yXW+PivwO2twrW/DUDb4Jmex9yrh8tysVSs/FMXcDlksfMAKUC9UpfxZ0umKar5gpzwW7E0K9ea6JovSugklHC5DegNF6QvEpdv3W83n/650et+tdNfLhcN9sXTr52te/7qq7n220gJrdKP8yvA/Z++Z6zy0QwABBBBAoF4BAsB65eiHAAIIINCcAlSFAAIItLFAxel8markT5VCWDdR92alVUrzBUbMXObS3t0jR+seaIk6Hnsb7+CGQHjgnMCjhs71nB1PeV+tVzs+b2iOHaKU3y+K+WtltfR+USpcLCrFx0rdEZEen186nC4r59Pkg1s9S7D97LvQnVXhC+a0YNdR6Q38Rrhc25Uy3iuEeZFQ5oaiaVzR+9jDn47ujO+K7kz8NLoz9bvIzuRdK/eNHunbN5QM7xwek1uEWU8N9EEAAQQQQKAWAQLAWrRo2zICFIoAAggggAACCLSbgP2FEXpnz2tVpaLPd22av6Nklqvv7Nuf/dV8x1qM/vbVffY36A6d7Tl98FzPMwd/5nuF1x/6b+nwXKc55G5VLm0VRuWjqlq5VJjmk4XH16/5gh3S6XbPN+yz12eHfQ/crbDO6SppgVBSeoO/Ebp2rSgX3iLLhefKcnWjUZGXRR6f+Hh0W+JnVuAXX7X1SMEO+KQQyh6HOwIIIIBA4wUYcW4CBIBzc6IVAggggAACCCCAAAJLKiA73RepwtgpUplWnjSPUtweQ/P4r436hr82j1EWtKtaIxyj53R1DqwNnjm4vmPjUKHzTYamf1UGQ9ul5tiqVSqfEqXSFaJcfKZQarX0+EPS7fVK3eF48MK++RlZq3sg8DNNJWVF83VktI6ev1mh381KiU+ahfFXmEJdZCjXFZEnp74c3jl8e2Rvcqh/10BebrFCQqs/t0UVYDIEEEAAgVkECABnAeIwAggggAACCCCAQCsItHeN6nLh1AJdb1LF8Xl99p/SdKUFe39aLOTfLLcKo1nUjl3hN/FFHWt9m+KejrdXAu6vO/zBGzVN+7YsFt8v8mPPU4XcWVbNvdLl8UmHw3ks7LMfrf3zuj0Q+Fk/TdOQbn9e64oe1bpiv9C8gW+a1eKbRSl/SUXKS6P9I++N7k3t6ds+dB+B37zI6YwAAgggsIgCBICLiM1UCCCAAAILLMDwCCCAQJsKJAe7zhHF8UeKakXWu0Qr2lJ6d+zu8vCRy1duOzpS7ziN6Ke2CO3wZuEdWtu5emCdf318rPMtppBfc3gDNwiH6ypRKb5T5bPPMfPZM5VRDUmHyy01XbeDPvveiBrsMWyTibtRrUqPL6v39t/p6OnfpnTH+8xi7qVVo3CJmTNeH33S6DciO5O/tt3kl4V9EpTdnzsCCCCAAAKtIkAA2CpnijrnLEBDBBBAAAEEEECgnQSspEnKrhVvt8Iw93zWpff0pURy4PUr9o/9ZT7j1NtXrRGOI2cHe4bWdvzj0G2BK5zj3Z/TAoGtDm/H1cKovFcVxy9S+bFHC6PaJZ1uz4IGfqZhCKHy0heIa529v9aCXd+WRvUN1WL2BVrVeGVk9MgnY9vjt/ZvHTgUOxAfl1t4W2+9551+CCCAwEIKMPbcBQgA525FSwQQQAABBBBAAAEEFl0gsd7/WCHF40WpKEWdmwx2F1W58LHef87uq3OImrspIeS9a4RncH3otPj64HMTvq4POzv839ODHd+TTtfHRLX0YnM8/QSzMNYrdX1BAj9hbRNX+CnTtG4l6fEl9Z7YHVp337eUJ/ifqlx5iTme3yyr5f8IP3n0m303DP2xe+s9GXlQVK2u3FpDgCoRQAABBOYgQAA4BySaIIAAAggggAACCDSzQHvXJkPR16n8WFAIK1KrZ6kujyHdvutNb+Wzcosw6xlirn3UFqEl1oQD8XX+fxhc6/s3rz/0Fc3n3yH9HV8RUr1OjY89y8ilTxHVsl9qmlNKTWvkW3qPr1PZm2FUpOYY1jp7b9P8HR+XmvrXaj632RzNXRl1Dn42tmfkltiexD3hncNjC21zfG08RwABBBBAYLEFCAAXW5z5EEAAAQQWRoBREUAAgTYUOLx5pVfr6DnHzKXrWp2SUmkd3T8TyeRbY9+Kj9c1yCydlP3W3ucGe4bWdZ4z9IuOLSpo7tD8oR26r+MTwjQvUeOZx5q5TLcwDJeV900Efgsc+hmqlE8Ll+f/pNvzGaGZL9HyuUsyxcEPhLclb7K/vGPibb1N9CUosxBzGAEEEEAAgXkLEADOm5ABmkmAWhBAAAEEEEAAgXYScJUL660wKyqVKWtdl30BnB5eeag6fPiN4ZuGB2vtP1N7+1t7j1ih3+D6ro1D/q7POKvuWzW//7ua0/NWVS6tMcYzp6hCzi+Vcthhn32fabz5HLPXqUzTVKVCXijzbuHxXi1cvpeahdxF+dzouyJ7sgd6d48cffg+UbIQ1Xzmoi8CCCCAQPMIUEltAgSAtXnRGgEEEEAAAQQQQACBRRPQe095lZlKuOqZUO/tG1Pp+Htj+/O319N/ch+1WeiH13V0D57XdV6yGP6iq+r5seZxf0fTtH9T5eLjzPFsjygX3FbYt6BX+dl1nRD6meb90um8UXp8r9KkY4MoOV4bvWlsZ/8txUOrDwr7gxMJ/Wy09ryzKgQQQACBOQoQAM4RimYIIIAAAggggAACzSjQvjUlL3lkv3S6nyjKtWdY0h+qqErlCwl39LvzuerNDv1Gz+nqHDq/8+xEKfJ5l8v9c03Tv6tM86VmpXimKuQ6RKW84Ff52Wf576FfPic0x93S3/E90dH1cuFxPluOO18W+cfstyM3Ze+aeHtv3R+YaM/EHQEEEEAAgfYTIABsv3PKihBAAIHlJ8CKEUAAgTYUUKJ0sZEdCdWcZbm9phDyGjNd+MBZW+8sixo3tUVoqQtDoaHzes5JlHq/UHaL20XF2KYqpZerSvFhqlwMCMMK/YSQ9iYWcJsI/ay00ZozL5XxV+nxfln6Oi/WnY5nlh36K6I7ktfFdmXujRxM5uSWhf2CkwVcJkMjgAACCCCw4AIEgAtOzASLJcA8CCCAAAIIIIBAOwlIb+dzzVTc+ve6tJZl362HWW5Kdyip6TcVipk32lfCzdL8ocNKCDmwqd+XWB98+tAvQ98ojZd/IwqZ7apUeLmoVs4QQgSkMh1WFXbmZz1YexbwNhH8VcplVcwflUreIB3OS01d/5eke/h10T2jN4V3Dg+s2nqkYBVilb6AhTA0AggggEBTClBU7QLWPyhq70QPBBBAAAEEEEAAAQQQWDiBw5uFV3P7HimVsjKuuc1jJX9Kc3vvqJYKrzp1TyY1Wy8rOZN/2CxciXUdD4tvDL1fM3J3mMX8flktXyo07RTp8vqllSZOJH7Wj9nGm8/xicBPakpIWVaVYkKYxveFrr9W+V1Pz5dzl0UP5Lf37cslz9oqyvOZh75tJcBiEEAAAQRqECAArAGLpggggAACCCCAAALNJNC+tbhTniepQiY05xVKTegdXYes8O5V/XvT98/UTz1JOBMb/bHE+s7Lw+Xoj41K/v9EpfwOYZiPFG6vz8r8dCvvm3PwONNcMx1T9nubnS5TuDxFIfUB6XIfMDX9DdLle1ol1LPJCv2+0r+3eL/9RR4zjcMxBBBAAAEEEJhdgABwdiNaIIAAAgg0swC1IYAAAm0ooNy+c430iHuuS5PBrqKRz36gd+fwHVP1UWuEY2htIDK4ofPi5Bmnblea+05lVv5XFcefIp2egNR1XT64TdW/UfvsqxSFx28IMKe6WwAAEABJREFUbzAr3L6/CN11jRTGZbrL+dRsueM5sT2pL0T3Ze7m7b2NEmccBBBAAAEEHhAgAHzAgZ8tLkD5CCCAAAIIIIBAuwgoIaQeXrHWCufmdhWe061UYWxPxTP6HauD1V1MbOrBL/NIbAw/IxE59fNad+Q31j/+v2NmRtarSjkkNd0pH9wmOizQDyWlEl6/IYPdac0b+D8hxf/IcuV8l6v8T5H04MsjO0e32p/p9/B9d5WOr3+BymFYBBBAAIEWF6D8+gSsfwPU15FeCCCAAAIIIIAAAggg0HiB1DldHcLhOn1OI0tNSI9/yKyW3rNqqyjYfezPD0xsCD0h8bsVW6odK25TTnlAZYZfYaYSMaFMO/TT7NzPbrtQdyWEEm6fKTvDWS0Q+o2omp9Rpez5Zq76zMi2obdG9iR/3L01lZEHRXWhamDcthZgcQgggAACNQpoNbanOQIIIIAAAggggAACTSDQviVU9PJpqpT3zyWkU5qmhKb9xWFa8Z79ZR6bel7tcj5itwh1/1Dls+804vefKUoFt5TagoV+E1/gYf+wQj/p7zC03v4xvWfFncLp/poq5p6v53JrIrsSb4ruTP00diA+Lq127Xv2WBkCCCCAAALNKUAA2JznhaoQQAABBOYiQBsEEECgDQWsLG2VKuQcc1qaaQrp9j5FRE77vgiGblfV6v+qkYFnqVQyaAVtCx/6KWVqnb0lR9/qw7I7+gMl5SfMVHJzsZx7RuSsQ/8e3ZG4qWffaNaqRc1pPTRCAAEEEEAAgQURIABcEFYGXUwB5kIAAQQQQAABBNpLwIxYAaA+lzVJZUozcdhvJg+fqXKpTuv1wod+pmlKj7+k9/Yf0ruiu1Sl9AZzNHV2suLaELn+yNuje0ZvWrX1yKjcIsy5rIE2CCCAAAIIzFWAdvULEADWb0dPBBBAAAEEEEAAAQQaLiCV5lLVSsPHrWdAdWyzQz+3t+Toit6vdXbvkFK8tZrNnpfOyBdEtg1+KbLzyF1nbb2zLHl7bz3M9KlNgNYIIIAAAnUIEADWgUYXBBBAAAEEEEAAgaUUaO+5lWna6Z9aqlUey/yUMg3N7S3oPX336d2xXVKzQr+x0U3jSf1F4e3xz/btGvrjw/fdVSL0W6ozxbwIIIAAAgjMXYAAcO5WtEQAAQQQaCYBakEAAQTaVUB3+IW0YrVFXJ86tpmmITRHTu/ouVPrinxbKPGaSmb0vKJRemFke/J/Y3tSf1h98L6iVd2SBZSLyMJUCCCAAAIItI0AAWDbnMrluRBWjQACCCCAAAIItIuA2iz0+IbOMzSv97nWmub2JSBWw3pvxzI/ZRqGMCrj0hf8kxbsukpp2iuMTHpTwrj38siu5FX9e0b+tGrrkUK989APAQQQQACBRggwxvwECADn50dvBBBAAAEEEEAAAQTqFlBCyMTmcGBwU89TEkb//+odXbdY+/5FWvvFAm0TwZ8d+pUKeely36UFQt+SXv9LjVJhY0I78uq+PaNbYwcy9561VZQXqASGRaBeAfohgAACCNQpQABYJxzdEEAAAQQQQAABBJZCoD3mVGuEY+D8rlOS5/e8WgnPft3XcYtQ6opqZvRUUSrqjV7lA6GfaapysSA17X7N13G98He80qwYaxP6wBXRXaM39O9N30/o12h5xkMAAQQQQKA5BAgAm+M8UAUCCCCAQC0CtEUAAQRaVODw5pXexMbw4+PB3s/qmuOnwuX+lBofe5rKDHeIQk6TQslGLW0i9LN/VMolYVQHhMu1W3gDr5dVsXY8PfTS2J7UtX370/cR+jVKnHEQQAABBBBoXgECwOY9N1Q2iwCHEUAAAQQQQACBVhBQQsjUhaFQYmP3emchd51S5VulMP9NlIsrVX7MIU2jYaGfsDY781OmYahSIS2UeZvm8b1X6Y51FW/qEiv0+3Lk5uzfVh8URaspNwQQQAABBFpCgCLnL0AAOH9DRkAAAQQQQAABBBBA4CQBO/gb3BAID60PXlYaLx9QRul6YZjnWaFct6hUGvrv8InQz/5RLhVFsXCvcDivku7A890+1/nhp6T+u++m3B9XbRWFk4pkBwKtI0ClCCCAAALzEGjoPzzmUQddEUAAAQQQQAABBBCYRaA1DqstQkue6+tPrAtcoarGzdKofklo2pOF1PxSNe4tvraGnfkpU1VFpTgqzeqPhc/3dhEInBuNZ14VuylzS9f2dFpuEabdljsCCCCAAAIILF8BAsDle+5ZOQIIINCaAlSNAAIINKmA2iz0obWdq4d+0flmQ8pblGF8WtP0x0nd4ZbW1siylZDKChWLqlr+m9S0L5mewEUOh29TdHf6M7E9mXvkHaLSyPkYCwEEEEAAAQRaW4AAsLXP37KtnoUjgAACCCCAAALNIqDWCMfR9cFHJnId7xS6eZOslD8opDxTOhwuK/eTjapTWZtwOE3hdGelUr9Uuv6fwulbFylmX9+/N/2jnn2jWWsy1aj5GAcBBBBAAIFmEKCGxggQADbGkVEQQAABBBBAAAEElpnAHzYL19A5/scmfJ0fckhtv6pW3i1M9TAr+HM2NPgTQgm3t6r5gknN4dwlhfw3wxU8P/qk0U/b3+IrD4rqMqNnuctPgBUjgAACCMxTQJtnf7ojgAACCCCAAAIIILAIAs0zxeF/Ft7BZ3uf2jsW+LjQ9T2qUnmjMo1Tpe5wNDT403Ql/Z0l6e/4m7X6LwmzerFymi8O70pu7d81MCy38Nl+lgs3BBBAAAEEEJiDAAHgHJBoggACCCDQJAKUgQACCCyhwMiG7o6hc/1nO7q6vigd+g5hmK8Wwlwpdb1hwZ+yNuFwm7KzN6P5g7epSvXdmlnalHT1vjGyI/njyNZkTgqhlpCBqRFAAAEEEECgBQUIAFvwpC33klk/AggggAACCCCwWAJW0iaPnB3sGTjH+7yqLr9pZW/XyWr5UiFF1Ar+dGltjajFmkcJt6+id8eOSJf7OnM8d4UqlZ4XeeLQJ8Lbhv961tY7y42YhzEQQAABBBBoJQFqbZwAAWDjLBkJAQQQQAABBBBAoE0ErEBOHl7X0Z1Y63+RM+j7rqbrX1Pl4gVC07qkpjUk+FP2JjUlg11Fvbvv90ponzByY883c9VXRncnr4vuTMTlFt7m2yZ/pFhG/QL0RAABBBBogAABYAMQGQIBBBBAAAEEEEBgIQUWd+yJ4O9c/6Uur/da4XR9XhXHz7Yq6LCCP01am/V8Xjc791NCmFp3LK2Hem4VlepbzHz6omhm8N2xXfGfxw7Ex6Xgbb7zQqYzAggggAACCJwgQAB4AgcvEEAAAQSaVoDCEEAAgQUWGD2nqzN+jv8yt893vfB4J4I/Va0EpZCNC/6kVtXDK45qod5vmePpl1ZyxReGHz/w+ej2xN3yoKgu8BIZHgEEEEAAAQSWqQAB4DI98a26bOpGAAEEEEAAAQQaLWB/ucfgOZ7Lqj7HNhEIfNYs5NeocjEgGxD8TVztZ/0QTndB6+n7rfT4P2yMDl9cHS+9OrZzeCff5tvos8l4CCCAAALtIsA6GitAANhYT0ZDAAEEEEAAAQQQaBGB5AW9waFne15ZdWp7tEDoc2a5tEYVxq3gT0l7m88yrMxPKdM0pTeQ1kK9B0wlX2OOJS6KpAY+ENs78gsr+MvPZ3z6IrBMBFgmAggggECDBAgAGwTJMAgggAACCCCAAAILIdD4Me3gb+Bs3xWGKW7WOrs+rcrl/6cKOb8UDQr+lKhIf+g+Gez6qiyXX1TNj7w49uTkN2O7MvdK3ubb+BPKiAgggAACCCAwqwAB4KxENEAAAQQQWHIBCkAAAQQaIGB/xt/gWv+bDSF/4gh2/I+olp5q5se88w3+lL2ZhqGUSGtu70HlcLy+UsysL2vi9ZG9o/v7d40Nyy3CbMASGAIBBBBAAAEEEKhLgACwLjY6LYUAcyKAAAIIIIAAAnMRUJcL5x82C5faLHQlhLS/1Te+oePdFa9+u+YLfEiUi481CzmPtI7V81ZfO++z396rquWi9TgkXa4fC+l8j2ka545nKxfFnpL64sq9Y39dtfVIQbAhgAACCCCAQM0CdGi8AAFg400ZEQEEEEAAAQQQQGCJBOLPiTwtmXvE7VHnw/+W9J91a/LFj9zh8nj+IJzu96hy8QxRzLvqCf4mQr9qtSI0PaEFQz+Rbu8HTc25TlfqSark3BjtyHy0/+b87asPptNyC1f7LdHpZ9r2EmA1CCCAAAINFCAAbCAmQyGAAAIIIIAAAgg0UqCOsRzOx0nd8RhjNL7KTB59hplKnK+K+ZgoFx11X+1XKeWFw/V94Qm8pKK0J5nZyoaIZ/j9/QdyPwrfnB+IHYiPy63CqKNauiCAAAIIIIAAAosiQAC4KMxMggACCCBQtwAdEUAAgRoEjML4Pun1pSc+18+oSmnfreSvhiEmmtpX/JnVyph0OG4wXO61pua5ILo7ed3KfaNHCPwmiPiBAAIIIIAAAi0kQADYQidrOZfK2hFAAAEEEEAAgbkI9OXTR41s6kYZ7K7jSzfs2FAzlaYPW/cvSbf3aUlf+tIV+8Z+2r9rIC+FUHOpgTYIIIAAAgggUL8APRdGgABwYVwZFQEEEEAAAQQQQGAJBORBUTUz8XcKj+8vSnfMObCz20pvICWk/KxU6p9igcyVsT2pP5y1VZSXYBlMicByF2D9CCCAAAINFiAAbDAowyGAAAIIIIAAAgg0QqD+Mfr25ZLVTPIyraN7SEk5YwioNF0JX6AsHK691fL4uVF/+o3RfZm7+Uy/+v3piQACCCCAAALNJ0AA2HznhIoQQAABBI4J8IgAAgjUKdC/c/j28ljqedIXuFP4OgwlHgwCpfXPX29AyVC4IkO9I9Lt+bGRH3+FKIgX9O/J3EHwVyc43RBAAAEEEECgqQWsfwE1dX0Uh4CAAAEEEEAAAQQQqEdg5e7Rn+WN/NOrpcL7hb/jZ6Kj5/fKE/hhtVT+78royHlGJvmoiGPo2f37s9+2v9ijnjnogwACCCCAAAKNE2CkhRMgAFw4W0ZGAAEEEEAAAQQQWGKB1dvT6RV7Rt8fveHI0yOPvu/x0W1Hn7Vi78jbVhzI3Gy/VZgr/pb4BDE9AicLsAcBBBBAYAEECAAXAJUhEUAAAQQQQAABBOYj0Pi+UggltwhTWo+NH50REUAAAQQQQACB5hYgAGzu80N1CCCAwPIVYOUIIIAAAggggAACCCCAAAINESAAbAgjgyyUAOMigAACCCCAAAIIIIAAAggggED7C7DChRUgAFxYX0ZHAAEEEEAAAQQQQAABBBCYmwCtEEAAAQQWSIAAcIFgGRYBBBBAAAEEEECgHgH6IIAAAggggAACCDRagACw0aKMhwACCCAwfwFGQAABBBBAAAEEEEAAAQQQaJgAAWDDKBmo0QKMhwACCCCAAAIIIIAAAggggAAC7S/AChdegABw4Y2ZAQEEEEAAAQQQQAABBBBAYGYBjiKAAIGVGlcAAA2qSURBVAIILKAAAeAC4jI0AggggAACCCCAQC0CtEUAAQQQQAABBBBYCAECwIVQZUwEEEAAgfoF6IkAAggggAACCCCAAAIIINBQAQLAhnIyWKMEGAcBBBBAAAEEEEAAAQQQQAABBNpfgBUujgAB4OI4MwsCCCCAAAIIIIAAAggggMDUAuxFAAEEEFhgAQLABQZmeAQQQAABBBBAAIG5CNAGAQQQQAABBBBAYKEECAAXSpZxEUAAAQRqF6AHAggggAACCCCAAAIIIIBAwwUIABtOyoDzFaA/AggggAACCCCAAAIIIIAAAgi0vwArXDwBAsDFs2YmBBBAAAEEEEAAAQQQQACBEwV4hQACCCCwCAIEgIuAzBQIIIAAAggggAACMwlwDAEEEEAAAQQQQGAhBQgAF1KXsRFAAAEE5i5ASwQQQAABBBBAAAEEEEAAgQURIABcEFYGrVeAfggggAACCCCAAAIIIIAAAggg0P4CrHBxBQgAF9eb2RBAAAEEEEAAAQQQQAABBB4Q4CcCCCCAwCIJEAAuEjTTIIAAAggggAACCEwlwD4EEEAAAQQQQACBhRYgAFxoYcZHAAEEEJhdgBYIIIAAAggggAACCCCAAAILJkAAuGC0DFyrAO0RQAABBBBAAAEEEEAAAQQQQKD9BVjh4gsQAC6+OTMigAACCCCAAAIIIIAAAstdgPUjgAACCCyiAAHgImIzFQIIIIAAAggggMDxAjxHAAEEEEAAAQQQWAwBAsDFUGYOBBBAAIHpBTiCAAIIIIAAAggggAACCCCwoAIEgAvKy+BzFaAdAggggAACCCCAAAIIIIAAAgi0vwArXBoBAsClcWdWBBBAAAEEEEAAAQQQQGC5CrBuBBBAAIFFFiAAXGRwpkMAAQQQQAABBBCwBbgjgAACCCCAAAIILJYAAeBiSTMPAggggMDJAuxBAAEEEEAAAQQQQAABBBBYcAECwAUnZoLZBDiOAAIIIIAAAggggAACCCCAAALtL8AKl06AAHDp7JkZAQQQQAABBBBAAAEEEFhuAqwXAQQQQGAJBAgAlwCdKRFAAAEEEEAAgeUtwOoRQAABBBBAAAEEFlOAAHAxtZkLAQQQQODvAjxDAAEEEEAAAQQQQAABBBBYFAECwEVhZpLpBNiPAAIIIIAAAggggAACCCCAAALtL8AKl1aAAHBp/ZkdAQQQQAABBBBAAAEEEFguAqwTAQQQQGCJBAgAlwieaRFAAAEEEEAAgeUpwKoRQAABBBBAAAEEFluAAHCxxZkPAQQQQEAIDBBAAAEEEEAAAQQQQAABBBZNgABw0aiZaLIArxFAAAEEEEAAAQQQQAABBBBAoP0FWOHSCxAALv05oAIEEEAAAQQQQAABBBBAoN0FWB8CCCCAwBIKEAAuIT5TI4AAAggggAACy0uA1SKAAAIIIIAAAggshQAB4FKoMycCCCCwnAVYOwIIIIAAAggggAACCCCAwKIKEAAuKjeTHRPgEQEEEEAAAQQQQAABBBBAAAEE2l+AFTaHAAFgc5wHqkAAAQQQQAABBBBAAAEE2lWAdSGAAAIILLEAAeASnwCmRwABBBBAAAEElocAq0QAAQQQQAABBBBYKgECwKWSZ14EEEBgOQqwZgQQQAABBBBAAAEEEEAAgUUXIABcdHImRAABBBBAAAEEEEAAAQQQQAABBNpfgBU2jwABYPOcCypBAAEEEEAAAQQQQAABBNpNgPUggAACCDSBAAFgE5wESkAAAQQQQAABBNpbgNUhgAACCCCAAAIILKUAAeBS6jM3AgggsJwEWCsCCCCAAAIIIIAAAggggMCSCBAALgn78p2UlSOAAAIIIIAAAggggAACCCCAQPsLsMLmEiAAbK7zQTUIIIAAAggggAACCCCAQLsIsA4EEEAAgSYRIABskhNBGQgggAACCCCAQHsKsCoEEEAAAQQQQACBpRYgAFzqM8D8CCCAwHIQYI0IIIAAAggggAACCCCAAAJLJkAAuGT0y29iVowAAggggAACCCCAAAIIIIAAAu0vwAqbT4AAsPnOCRUhgAACCCCAAAIIIIAAAq0uQP0IIIAAAk0kQADYRCeDUhBAAAEEEEAAgfYSYDUIIIAAAggggAACzSBAANgMZ4EaEEAAgXYWYG0IIIAAAggggAACCCCAAAJLKkAAuKT8y2dyVooAAggggAACCCCAAAIIIIAAAu0vwAqbU4AAsDnPC1UhgAACCCCAAAIIIIAAAq0qQN0IIIAAAk0mQADYZCeEchBAAAEEEEAAgfYQYBUIIIAAAggggAACzSJAANgsZ4I6EEAAgXYUYE0IIIAAAggggAACCCCAAAJLLkAAuOSnoP0LYIUIIIAAAggggAACCCCAAAIIIND+AqyweQUIAJv33FAZAggggAACCCCAAAIIINBqAtSLAAIIINCEAgSATXhSKAkBBBBAAAEEEGhtAapHAAEEEEAAAQQQaCYBAsBmOhvUggACCLSTAGtBAAEEEEAAAQQQQAABBBBoCgECwKY4De1bBCtDAAEEEEAAAQQQQAABBBBAAIH2F2CFzS1AANjc54fqEEAAAQQQQAABBBBAAIFWEaBOBBBAAIEmFSAAbNITQ1kIIIAAAggggEBrClA1AggggAACCCCAQLMJEAA22xmhHgQQQKAdBFgDAggggAACCCCAAAIIIIBA0wgQADbNqWi/QlgRAggggAACCCCAAAIIIIAAAgi0vwArbH4BAsDmP0dUiAACCCCAAAIIIIAAAgg0uwD1IYAAAgg0sQABYBOfHEpDAAEEEEAAAQRaS4BqEUAAAQQQQAABBJpRgACwGc8KNSGAAAKtLEDtCCCAAAIIIIAAAggggAACTSVAANhUp6N9imElCCCAAAIIIIAAAggggAACCCDQ/gKssDUECABb4zxRJQIIIIAAAggggAACCCDQrALUhQACCCDQ5AIEgE1+gigPAQQQQAABBBBoDQGqRAABBBBAAAEEEGhWAQLAZj0z1IUAAgi0ogA1I4AAAggggAACCCCAAAIINJ0AAWDTnZLWL4gVIIAAAggggAACCCCAAAIIIIBA+wuwwtYRIABsnXNFpQgggAACCCCAAAIIIIBAswlQDwIIIIBACwgQALbASaJEBBBAAAEEEECguQWoDgEEEEAAAQQQQKCZBQgAm/nsUBsCCCDQSgLUigACCCCAAAIIIIAAAggg0JQCBIBNeVpatygqRwABBBBAAAEEEEAAAQQQQACB9hdgha0lQADYWueLahFAAAEEEEAAAQQQQACBZhGgDgQQQACBFhEgAGyRE0WZCCCAAAIIIIBAcwpQFQIIIIAAAggggECzCxAANvsZoj4EEECgFQSoEQEEEEAAAQQQQAABBBBAoGkFCACb9tS0XmFUjAACCCCAAAIIIIAAAggggAAC7S/ACltPgACw9c4ZFSOAAAIIIIAAAggggAACSy3A/AgggAACLSRAANhCJ4tSEUAAAQQQQACB5hKgGgQQQAABBBBAAIFWECAAbIWzRI0IIIBAMwtQGwIIIIAAAggggAACCCCAQFMLEAA29elpneKoFAEEEEAAAQQQQAABBBBAAAEE2l+AFbamAAFga543qkYAAQQQQAABBBBAAAEElkqAeRFAAAEEWkyAALDFThjlIoAAAggggAACzSFAFQgggAACCCCAAAKtIkAA2CpnijoRQACBZhSgJgQQQAABBBBAAAEEEEAAgaYXIABs+lPU/AVSIQIIIIAAAggggAACCCCAAAIItL8AK2xdAQLA1j13VI4AAggggAACCCCAAAIILLYA8yGAAAIItKAAAWALnjRKRgABBBBAAAEEllaA2RFAAAEEEEAAAQRaSYAAsJXOFrUigAACzSRALQgggAACCCCAAAIIIIAAAi0hQADYEqepeYukMgQQQAABBBBAAAEEEEAAAQQQaH8BVtjaAgSArX3+qB4BBBBAAAEEEEAAAQQQWCwB5kEAAQQQaFEBAsAWPXGUjQACCCCAAAIILI0AsyKAAAIIIIAAAgi0mgABYKudMepFAAEEmkGAGhBAAAEEEEAAAQQQQAABBFpGgACwZU5V8xVKRQgggAACCCCAAAIIIIAAAggg0P4CrLD1BQgAW/8csgIEEEAAAQQQQAABBBBAYKEFGB8BBBBAoIUFCABb+ORROgIIIIAAAgggsLgCzIYAAggggAACCCDQigIEgK141qgZAQQQWEoB5kYAAQQQQAABBBBAAAEEEGgpAQLAljpdzVMslSCAAAIIIIAAAggggAACCCCAQPsLsML2ECAAbI/zyCoQQAABBBBAAAEEEEAAgYUSYFwEEEAAgRYXIABs8RNI+QgggAACCCCAwOIIMAsCCCCAAAIIIIBAqwoQALbqmaNuBBBAYCkEmBMBBBBAAAEEEEAAAQQQQKDlBAgAW+6ULX3BVIAAAggggAACCCCAAAIIIIAAAu0vwArbR4AAsH3OJStBAAEEEEAAAQQQQAABBBotwHgIIIAAAm0gQADYBieRJSCAAAIIIIAAAgsrwOgIIIAAAggggAACrSxAANjKZ4/aEUAAgcUUYC4EEEAAAQQQQAABBBBAAIGWFCAAbMnTtnRFMzMCCCCAAAIIIIAAAggggAACCLS/ACtsLwECwPY6n6wGAQQQQAABBBBAAAEEEGiUAOMggAACCLSJwP8HAAD//64iuEMAAAAGSURBVAMApCVBuog7ReEAAAAASUVORK5CYII="
         alt="ZEVION Labs"
         class="welcome-logo">
    <div class="welcome-status">
      <span class="dot"></span>
      <span id="welcome-platform-text">Local AI Agent</span>
    </div>
    <div class="welcome-input-area">
      <form class="welcome-form" id="welcome-form" onsubmit="event.preventDefault(); handleSend();">
        <div class="input-box-wrapper">
          <textarea id="user-input" rows="1"
            placeholder="Type a command or question..."
            onkeydown="handleKeyDown(event)"></textarea>
          <span class="char-counter" id="char-counter">0</span>
        </div>
        <button type="submit" id="welcome-send-btn">Send</button>
      </form>
      <div class="welcome-quick-btns">
        <button type="button" class="welcome-quick-btn" onclick="triggerHistory()">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
            <path d="M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0 0 13 21a9 9 0 0 0 0-18zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z"/>
          </svg>
          History
        </button>
        <button type="button" class="welcome-quick-btn" data-quick-actions-trigger onclick="toggleQuickActions(this)">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
            <path d="M4 6h16v2H4zm0 5h16v2H4zm0 5h16v2H4z"/>
          </svg>
          Quick Actions
        </button>
        <button type="button" class="welcome-quick-btn" onclick="sendPromptFromPanel('show ram status and disk space')">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
            <path d="M1 9l2 2c4.97-4.97 13.03-4.97 18 0l2-2C16.93 2.93 7.08 2.93 1 9zm8 8l3 3 3-3a4.237 4.237 0 0 0-6 0zm-4-4l2 2a7.074 7.074 0 0 1 10 0l2-2C15.14 9.14 8.87 9.14 5 13z"/>
          </svg>
          Status
        </button>
      </div>
    </div>
  </div>

  <main id="chat-container" class="chat-hidden">
    <div class="msg-wrapper msg-ember">
      <div class="bubble">
        👋 <strong>Welcome to EmberOS Web Terminal!</strong><br>
        Ask system commands, inspect resources, or manage tasks. Your request executes securely on this machine.
        <span class="msg-timestamp" id="welcome-timestamp"></span>
      </div>
    </div>
  </main>

  <footer id="main-footer" class="footer-hidden">
    <div class="quick-actions-wrapper">
      <button type="button" class="quick-actions-btn" id="quick-actions-btn"
              data-quick-actions-trigger onclick="toggleQuickActions(this)">
        Quick Actions
      </button>
    </div>
    <form class="input-form" id="chat-form" onsubmit="event.preventDefault(); handleSend();">
      <div class="input-box-wrapper">
        <textarea id="user-input-footer" rows="1"
          placeholder="Type a command or question..."
          onkeydown="handleKeyDown(event)"></textarea>
        <span class="char-counter" id="char-counter-footer">0</span>
      </div>
      <button type="submit" id="send-btn">Send</button>
    </form>
  </footer>

  <div id="toast" class="toast" hidden></div>
  <div class="quick-actions-panel" id="quick-actions-panel-global" hidden>
    <div class="quick-actions-grid">
      <button type="button" class="qa-item clear-action" onclick="clearChat()">Clear Chat</button>
      <button type="button" class="qa-item" onclick="sendPromptFromPanel('show ram status and disk space')">RAM &amp; Disk</button>
      <button type="button" class="qa-item" onclick="sendPromptFromPanel('what is my system uptime?')">Uptime</button>
      <button type="button" class="qa-item" onclick="sendPromptFromPanel('show my tasks')">Tasks</button>
      <button type="button" class="qa-item" onclick="sendPromptFromPanel('check cpu info')">CPU Info</button>
      <button type="button" class="qa-item" onclick="sendPromptFromPanel('check cpu temperature')">Temperature</button>
      <button type="button" class="qa-item" onclick="sendPromptFromPanel('show disk space')">Disk Space</button>
      <button type="button" class="qa-item" onclick="sendPromptFromPanel('/memory')">Memory</button>
      <button type="button" class="qa-item" onclick="sendPromptFromPanel('/memory search')">Search History</button>
      <button type="button" class="qa-item" onclick="sendPromptFromPanel('summarize &quot;experiments/fixtures/sample-summary.txt&quot;')">Summarize Sample</button>
      <button type="button" class="qa-item" onclick="sendPromptFromPanel('find large files')">Large Files</button>
    </div>
  </div>

  <script>
    // Manage authentication token via query param or sessionStorage
    const urlParams = new URLSearchParams(window.location.search);
    let token = urlParams.get('token');
    if (token) {
      sessionStorage.setItem('ember_token', token);
      // Strip token from browser address bar for security
      window.history.replaceState({}, document.title, window.location.pathname);
    } else {
      token = sessionStorage.getItem('ember_token');
    }

    if (!token) {
      token = prompt("Please enter the EmberOS Access Token displayed in the server terminal:");
      if (token) {
        sessionStorage.setItem('ember_token', token.trim());
      }
    }

    const chatContainer = document.getElementById('chat-container');
    const welcomeInput = document.getElementById('user-input');
    const footerInput = document.getElementById('user-input-footer');
    const sendBtn = document.getElementById('send-btn');
    const welcomeSendBtn = document.getElementById('welcome-send-btn');
    let chatStarted = false;

    function escapeHtml(unsafe) {
      return unsafe
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }

    function formatResponseText(text) {
      if (!text) return "<em>(No response returned)</em>";
      const escaped = escapeHtml(text);
      return escaped.replace(/\n/g, '<br>');
    }

    function appendMessage(role, text, meta) {
      const wrapper = document.createElement('div');
      wrapper.className = `msg-wrapper msg-${role}`;

      const bubble = document.createElement('div');
      bubble.className = 'bubble';
      bubble.innerHTML = formatResponseText(text);

      if (role === 'ember') {
        const copyBtn = document.createElement('button');
        copyBtn.className = 'copy-btn';
        copyBtn.type = 'button';
        copyBtn.textContent = 'Copy';
        copyBtn.onclick = async () => {
          try {
            await navigator.clipboard.writeText(String(text ?? ''));
            copyBtn.textContent = 'Copied';
            showToast('Copied');
            setTimeout(() => { copyBtn.textContent = 'Copy'; }, 2000);
          } catch (err) {
            showToast('Copy failed');
          }
        };
        bubble.appendChild(copyBtn);
      }

      const timestamp = document.createElement('span');
      timestamp.className = 'msg-timestamp';
      timestamp.textContent = new Date().toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit'
      });
      bubble.appendChild(timestamp);
      wrapper.appendChild(bubble);

      // Append Translucent Benchmark Dropdown if meta is provided
      if (meta && role === 'ember') {
        const details = document.createElement('details');
        details.className = 'meta-dropdown';

        const summary = document.createElement('summary');
        summary.className = 'meta-summary';

        const confText = meta.confidence !== null && meta.confidence !== undefined
          ? `Conf: ${(meta.confidence * 100).toFixed(0)}%`
          : 'Conf: N/A';
        const toolText = meta.tool ? `Tool: ${meta.tool}` : `Route: ${meta.route}`;
        const timeText = meta.elapsed_seconds ? `${meta.elapsed_seconds.toFixed(2)}s` : '';

        summary.innerHTML = `
          <div class="meta-pills">
            <span class="pill accent">⚡ ${timeText}</span>
            <span class="pill">🎯 ${confText}</span>
            <span class="pill">🔧 ${escapeHtml(toolText)}</span>
            <span class="pill">💾 RSS: ${meta.rss_current_mb || 'N/A'}MB</span>
          </div>
          <span>▼</span>
        `;
        details.appendChild(summary);

        const content = document.createElement('div');
        content.className = 'meta-details-content';
        content.innerHTML = `
          <div class="stat-item">
            <span class="stat-label">Response Time</span>
            <span class="stat-val">${meta.elapsed_seconds ? meta.elapsed_seconds.toFixed(3) + 's' : 'N/A'}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">Confidence</span>
            <span class="stat-val">${meta.confidence !== null ? meta.confidence.toFixed(4) : 'None'}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">Route</span>
            <span class="stat-val">${escapeHtml(meta.route || 'N/A')}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">Tool</span>
            <span class="stat-val">${escapeHtml(meta.tool || 'N/A')}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">Memory Used</span>
            <span class="stat-val">${meta.rss_current_mb || '0'} MB (delta: ${meta.rss_delta_mb || '0'} MB)</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">Peak Memory</span>
            <span class="stat-val">${meta.peak_total_mib || '0'} MiB (Children: ${meta.peak_children_mib || '0'} MiB)</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">System RAM</span>
            <span class="stat-val">${meta.system_ram_used_mb || '0'} / ${meta.system_ram_total_mb || '0'} MB (${meta.system_ram_percent || '0'}%)</span>
          </div>
        `;
        details.appendChild(content);
        wrapper.appendChild(details);
      }

      chatContainer.appendChild(wrapper);
      chatContainer.scrollTop = chatContainer.scrollHeight;
    }

    function appendTypingIndicator() {
      const wrapper = document.createElement('div');
      wrapper.className = 'msg-wrapper msg-ember typing-wrapper';
      wrapper.innerHTML = `
        <div class="bubble typing">
          <span class="dot"></span>
          <span class="dot"></span>
          <span class="dot"></span>
        </div>
      `;
      chatContainer.appendChild(wrapper);
      chatContainer.scrollTop = chatContainer.scrollHeight;
      return wrapper;
    }

    let welcomeHideTimer = null;
    let footerFocusTimer = null;
    let chatSessionId = 0;

    function updateCharacterCounter(input, counter) {
      if (!input || !counter) return;
      const count = input.value.length;
      counter.textContent = count;
      counter.style.color = count > 60000 ? 'var(--error)' :
                            count > 50000 ? 'var(--warning)' :
                            'var(--text-dim)';
    }

    function resizeInput(input) {
      if (!input) return;
      input.style.height = 'auto';
      input.style.height = input.scrollHeight + 'px';
    }

    function resetInput(input, counter) {
      if (!input) return;
      input.value = '';
      input.style.height = 'auto';
      updateCharacterCounter(input, counter);
    }

    function getActiveInput() {
      return chatStarted ? footerInput : welcomeInput;
    }

    function setSendButtonsDisabled(disabled) {
      sendBtn.disabled = disabled;
      welcomeSendBtn.disabled = disabled;
    }

    function startChat() {
      if (chatStarted) return;
      chatStarted = true;

      const welcome = document.getElementById('welcome-screen');
      welcome.style.opacity = '0';
      welcome.style.transform = 'translateY(-20px)';
      welcome.style.pointerEvents = 'none';
      clearTimeout(welcomeHideTimer);
      welcomeHideTimer = setTimeout(() => {
        welcome.classList.add('hidden');
      }, 300);

      const header = document.getElementById('main-header');
      header.classList.remove('header-hidden');
      header.style.opacity = '0';
      header.style.transform = 'translateY(-8px)';
      requestAnimationFrame(() => {
        header.style.opacity = '1';
        header.style.transform = 'translateY(0)';
      });

      const chat = document.getElementById('chat-container');
      chat.classList.remove('chat-hidden');

      const footer = document.getElementById('main-footer');
      footer.classList.remove('footer-hidden');
      footer.style.opacity = '0';
      requestAnimationFrame(() => {
        footer.style.opacity = '1';
      });

      clearTimeout(footerFocusTimer);
      footerFocusTimer = setTimeout(() => footerInput.focus(), 100);
    }

    function resetToWelcome() {
      chatSessionId += 1;
      chatStarted = false;
      closeQuickActions();

      chatContainer.querySelectorAll('.msg-wrapper').forEach((message) => {
        message.remove();
      });

      resetInput(welcomeInput, document.getElementById('char-counter'));
      resetInput(footerInput, document.getElementById('char-counter-footer'));
      setSendButtonsDisabled(false);

      const header = document.getElementById('main-header');
      header.classList.add('header-hidden');
      header.style.opacity = '';
      header.style.transform = '';

      const chat = document.getElementById('chat-container');
      chat.classList.add('chat-hidden');

      const footer = document.getElementById('main-footer');
      footer.classList.add('footer-hidden');
      footer.style.opacity = '';

      const welcome = document.getElementById('welcome-screen');
      clearTimeout(welcomeHideTimer);
      welcome.classList.remove('hidden');
      welcome.style.opacity = '0';
      welcome.style.transform = 'translateY(-20px)';
      welcome.style.pointerEvents = 'auto';
      requestAnimationFrame(() => {
        welcome.style.opacity = '1';
        welcome.style.transform = 'translateY(0)';
      });
      setTimeout(() => welcomeInput.focus(), 50);
    }

    async function handleSend() {
      const activeInput = getActiveInput();
      const query = activeInput.value.trim();
      if (!query) return;

      const requestSession = chatSessionId;
      startChat();
      appendMessage('user', query);
      resetInput(welcomeInput, document.getElementById('char-counter'));
      resetInput(footerInput, document.getElementById('char-counter-footer'));

      setSendButtonsDisabled(true);
      const typingEl = appendTypingIndicator();
      const activeToken = sessionStorage.getItem('ember_token') || '';

      try {
        const response = await fetch('/api/chat', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Ember-Token': activeToken
          },
          body: JSON.stringify({ query: query })
        });

        if (typingEl) typingEl.remove();
        if (requestSession !== chatSessionId) return;

        if (response.status === 401) {
          appendMessage('ember', '❌ <strong>Authentication Failed</strong>: Invalid or missing token. Please reload and enter a valid access token.');
          return;
        }

        if (response.status === 403) {
          const errData = await response.json();
          appendMessage('ember', '⚠️ <strong>Security Block</strong>: ' + errData.error);
          return;
        }

        if (!response.ok) {
          const errText = await response.text();
          appendMessage('ember', '❌ <strong>Request Error (' + response.status + ')</strong>: ' + escapeHtml(errText));
          return;
        }

        const data = await response.json();
        appendMessage('ember', data.answer, data.meta);
        if (data.meta && data.meta.route === 'tool_call' && data.answer && !data.answer.includes('FAILED')) {
          showToast('Done');
        }
      } catch (err) {
        if (typingEl) typingEl.remove();
        if (requestSession === chatSessionId) {
          appendMessage('ember', '❌ <strong>Network / Connection Error</strong>: ' + escapeHtml(err.message));
        }
      } finally {
        if (requestSession === chatSessionId) {
          setSendButtonsDisabled(false);
          getActiveInput().focus();
        }
      }
    }

    function handleKeyDown(event) {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        handleSend();
      }
    }

    function sendPrompt(text) {
      const activeInput = getActiveInput();
      activeInput.value = text;
      handleSend();
    }

    function triggerHistory() {
      const activeInput = getActiveInput();
      activeInput.value = '/memory';
      handleSend();
    }

    function positionQuickActions() {
      const panel = document.getElementById('quick-actions-panel-global');
      const anchor = chatStarted
        ? document.getElementById('main-footer')
        : document.querySelector('.welcome-input-area');
      if (!panel || !anchor) return;

      const rect = anchor.getBoundingClientRect();
      const offset = window.innerHeight - rect.top + 12;
      panel.style.bottom = Math.max(20, offset) + 'px';
    }

    function closeQuickActions() {
      const panel = document.getElementById('quick-actions-panel-global');
      if (!panel) return;
      panel.hidden = true;
      document.querySelectorAll('[data-quick-actions-trigger]').forEach((trigger) => {
        trigger.classList.remove('active');
      });
    }

    function toggleQuickActions(trigger) {
      const panel = document.getElementById('quick-actions-panel-global');
      if (!panel) return;

      if (!panel.hidden) {
        closeQuickActions();
        return;
      }

      panel.hidden = false;
      positionQuickActions();
      if (trigger) trigger.classList.add('active');
    }

    document.addEventListener('mousedown', (event) => {
      const panel = document.getElementById('quick-actions-panel-global');
      if (!panel || panel.hidden) return;

      const target = event.target;
      const clickedTrigger = target && target.closest
        ? target.closest('[data-quick-actions-trigger]')
        : null;
      if (!panel.contains(target) && !clickedTrigger) {
        closeQuickActions();
      }
    });

    window.addEventListener('resize', () => {
      const panel = document.getElementById('quick-actions-panel-global');
      if (panel && !panel.hidden) positionQuickActions();
    });

    function sendPromptFromPanel(text) {
      closeQuickActions();
      const activeInput = getActiveInput();
      activeInput.value = text;
      handleSend();
      setTimeout(() => getActiveInput().focus(), 120);
    }

    function clearChat() {
      resetToWelcome();
      showToast('Chat cleared');
    }

    function showToast(message) {
      const toast = document.getElementById('toast');
      toast.textContent = message;
      toast.hidden = false;
      toast.style.animation = 'none';
      void toast.offsetHeight;
      toast.style.animation = 'fadeInOut 2.5s ease forwards';
      clearTimeout(showToast.timer);
      showToast.timer = setTimeout(() => { toast.hidden = true; }, 2500);
    }

    async function fetchStatus() {
      const activeToken = sessionStorage.getItem('ember_token') || '';
      try {
        const res = await fetch('/api/status', {
          headers: { 'X-Ember-Token': activeToken }
        });
        if (res.ok) {
          const data = await res.json();
          const os = data.os || 'Linux';
          const machine = data.machine || 'aarch64';
          const statusText = (os + ' ' + machine).trim();
          const welcomeEl = document.getElementById('welcome-platform-text');
          const headerEl = document.getElementById('header-platform-text');
          if (welcomeEl) welcomeEl.textContent = statusText + ' \u00b7 Local AI Agent';
          if (headerEl) headerEl.textContent = statusText;
        }
      } catch (e) {
        // Keep the default labels if status lookup fails.
      }
    }

    window.addEventListener('load', () => {
      const welcomeTimestamp = document.getElementById('welcome-timestamp');
      if (welcomeTimestamp) {
        welcomeTimestamp.textContent = new Date().toLocaleTimeString([], {
          hour: '2-digit',
          minute: '2-digit'
        });
      }
      updateCharacterCounter(welcomeInput, document.getElementById('char-counter'));
      updateCharacterCounter(footerInput, document.getElementById('char-counter-footer'));
      welcomeInput.focus();
      fetchStatus();
    });

    welcomeInput.addEventListener('input', () => {
      resizeInput(welcomeInput);
      updateCharacterCounter(welcomeInput, document.getElementById('char-counter'));
    });

    footerInput.addEventListener('input', () => {
      resizeInput(footerInput);
      updateCharacterCounter(footerInput, document.getElementById('char-counter-footer'));
    });
  </script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP Request Handler with Security & Benchmarks
# ---------------------------------------------------------------------------
class EmberHTTPHandler(BaseHTTPRequestHandler):
    server_version = "EmberOS-HTTP/1.0"

    def log_message(self, format, *args):
        # Suppress default noisy console logs; custom audit log is used instead
        pass

    def _client_ip(self) -> str:
        return self.client_address[0] if self.client_address else "unknown"

    def _send_json(self, status_code: int, data: dict, origin: str = "*"):
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(payload)

    def _check_auth(self) -> bool:
        """Enforce X-Ember-Token on /api/* endpoints."""
        token = self.headers.get("X-Ember-Token", "")
        expected = self.server.ctx.auth_token
        # Constant-time comparison to protect against timing attacks
        return secrets.compare_digest(token.strip(), expected.strip())

    def do_OPTIONS(self):
        """Preflight CORS handling: reject cross-origin API calls."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Ember-Token")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self):
        client_ip = self._client_ip()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            # Serve the SPA
            content = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)
            _log_web_audit(client_ip, "GET /", "", "", 200)
            return

        if path == "/api/status":
            if not self._check_auth():
                self._send_json(401, {"error": "Unauthorized: valid X-Ember-Token required."})
                _log_web_audit(client_ip, "GET /api/status", "", "", 401, "AUTH_FAIL")
                return

            vm = psutil.virtual_memory()
            status_data = {
                "os": platform.system(),
                "machine": platform.machine(),
                "python": sys.version.split()[0],
                "ram_used_mb": round(vm.used / (1024 ** 2), 1),
                "ram_total_mb": round(vm.total / (1024 ** 2), 1),
                "ram_percent": vm.percent,
                "multistep": self.server.ctx.multistep_enabled,
            }
            self._send_json(200, status_data)
            _log_web_audit(client_ip, "GET /api/status", "", "", 200)
            return

        # 404 for unknown paths
        self.send_error(404, "Not Found")
        _log_web_audit(client_ip, f"GET {path}", "", "", 404)

    def do_POST(self):
        client_ip = self._client_ip()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path != "/api/chat":
            self.send_error(404, "Not Found")
            _log_web_audit(client_ip, f"POST {path}", "", "", 404)
            return

        # Rate Limiting Check
        if not self.server.ctx.rate_limiter.is_allowed(client_ip):
            self._send_json(429, {"error": "Too Many Requests. Rate limit exceeded (40 req/min)."})
            _log_web_audit(client_ip, "POST /api/chat", "", "", 429, "RATE_LIMITED")
            return

        # Authentication Check
        if not self._check_auth():
            self._send_json(401, {"error": "Unauthorized: valid X-Ember-Token header required."})
            _log_web_audit(client_ip, "POST /api/chat", "", "", 401, "AUTH_FAIL")
            return

        # Payload Size Limit Check (Pi Protection)
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            content_length = 0

        if content_length <= 0:
            self._send_json(400, {"error": "Missing or invalid Content-Length."})
            return

        if content_length > MAX_BODY_SIZE:
            self._send_json(413, {"error": f"Payload Too Large. Max allowed is {MAX_BODY_SIZE} bytes."})
            _log_web_audit(client_ip, "POST /api/chat", "", "", 413, f"OVERSIZED ({content_length} bytes)")
            return

        # Read JSON body safely
        try:
            raw_body = self.rfile.read(content_length)
            data = json.loads(raw_body.decode("utf-8"))
            query = str(data.get("query", "")).strip()
        except Exception as e:
            self._send_json(400, {"error": f"Invalid JSON body: {e}"})
            return

        if not query:
            self._send_json(400, {"error": "Query cannot be empty."})
            return

        # -------------------------------------------------------------------
        # Benchmark & Tool Execution with Web-Safe Filter
        # -------------------------------------------------------------------
        rss_before, _ = _memory_snapshot()
        started = time.perf_counter()
        sample = RequestMemorySampler(_PROCESS)

        try:
            with sample:
                # Pre-flight Tool Routing Check for the Web policy. The
                # registry and run_ember gate both enforce this before tool
                # functions run; this result check is defense-in-depth.
                result = handle_request(
                    query,
                    self.server.ctx.registry,
                    self.server.ctx.memory,
                    reply_renderer=self.server.ctx.reply_renderer,
                    multi_step_mode=self.server.ctx.multistep_enabled
                )
        except Exception as e:
            elapsed = time.perf_counter() - started
            self._send_json(500, {
                "answer": f"Request failed with internal error: {e}",
                "meta": {"elapsed_seconds": elapsed, "route": "error"}
            })
            _log_web_audit(client_ip, "POST /api/chat", query, "error", 500, str(e))
            return

        elapsed = time.perf_counter() - started

        # A destructive request is held by run_ember until the next message
        # contains one of the exact confirmation words. Return the prompt
        # directly so it is never mistaken for a blocked or empty response.
        if result.get("route") == "confirmation_required":
            confirm_msg = result.get("display_response") or result.get("response", "")
            rss_current, vm = _memory_snapshot()
            confirm_tool = result.get("tool") or ""
            confirm_meta = {
                "elapsed_seconds": round(elapsed, 3),
                "route": "confirmation_required",
                "tool": confirm_tool,
                "confidence": None,
                "rss_current_mb": round(rss_current / (1024 ** 2), 1),
                "rss_delta_mb": round((rss_current - rss_before) / (1024 ** 2), 2),
                "peak_total_mib": round(sample.peak_total / 2**20, 1),
                "peak_parent_mib": round(sample.parent_at_peak / 2**20, 1),
                "peak_children_mib": round(sample.children_at_peak / 2**20, 1),
                "system_ram_used_mb": round(vm.used / (1024 ** 2), 1),
                "system_ram_total_mb": round(vm.total / (1024 ** 2), 1),
                "system_ram_percent": vm.percent,
            }
            _log_web_audit(
                client_ip, "POST /api/chat", query, confirm_tool, 200,
                "CONFIRM_REQUIRED",
            )
            self._send_json(200, {"answer": confirm_msg, "meta": confirm_meta})
            return

        # Check if executed tool was blocked/disallowed in Web mode
        tool_name = result.get("tool") or ""
        called_tools = [tool_name]
        if result.get("route") == "multi_tool_call":
            called_tools = [item.get("tool") for item in result.get("results", []) if item.get("tool")]

        # Block any result containing permanently blocked or unknown tools.
        # Confirmation-required tools are valid here because handle_request
        # either returned a confirmation prompt or executed a confirmed replay.
        is_blocked = any(
            t in WEB_BLOCKED_ALWAYS
            or (t and t not in WEB_SAFE_TOOLS and t not in WEB_CONFIRM_TOOLS)
            for t in called_tools
        )
        if is_blocked:
            clean_answer = (
                f"⚠️ Security restriction: The requested tool ({tool_name or 'system action'}) "
                "is restricted in Web mode to protect this host. "
                "Please use the physical terminal CLI for destructive or system-level actions."
            )
            _log_web_audit(client_ip, "POST /api/chat", query, tool_name, 403, "BLOCKED_TOOL")
            self._send_json(403, {
                "error": clean_answer,
                "answer": clean_answer,
                "meta": {
                    "elapsed_seconds": round(elapsed, 3),
                    "tool": tool_name,
                    "route": "blocked_web_tool",
                    "confidence": result.get("confidence") or result.get("needle_confidence")
                }
            })
            return

        # Prepare clean answer
        clean_answer = result.get("display_response") or result.get("response") or ""

        # Prepare benchmark metadata
        rss_current, vm = _memory_snapshot()
        delta_mb = round((rss_current - rss_before) / (1024 ** 2), 2)
        rss_current_mb = round(rss_current / (1024 ** 2), 1)

        meta = {
            "elapsed_seconds": round(elapsed, 3),
            "confidence": result.get("confidence") if result.get("confidence") is not None else result.get("needle_confidence"),
            "tool": tool_name or (result.get("calls")[0]["name"] if result.get("calls") else None),
            "route": result.get("route"),
            "rss_current_mb": rss_current_mb,
            "rss_delta_mb": delta_mb,
            "peak_total_mib": round(sample.peak_total / 2**20, 1),
            "peak_parent_mib": round(sample.parent_at_peak / 2**20, 1),
            "peak_children_mib": round(sample.children_at_peak / 2**20, 1),
            "system_ram_used_mb": round(vm.used / (1024 ** 2), 1),
            "system_ram_total_mb": round(vm.total / (1024 ** 2), 1),
            "system_ram_percent": vm.percent,
        }

        _log_web_audit(client_ip, "POST /api/chat", query, tool_name, 200, f"{elapsed:.2f}s")

        self._send_json(200, {
            "answer": clean_answer,
            "meta": meta
        })


# ---------------------------------------------------------------------------
# Server Launch & CLI Options
# ---------------------------------------------------------------------------
def run_server(host: str = "127.0.0.1", port: int = 8080):
    auth_token = secrets.token_urlsafe(32)
    ctx = EmberWebContext(auth_token)

    server = HTTPServer((host, port), EmberHTTPHandler)
    server.ctx = ctx

    access_url = f"http://{'localhost' if host == '127.0.0.1' else host}:{port}/?token={auth_token}"

    print("=" * 72)
    print("  🔥 EmberOS Web GUI Server")
    print(f"  Platform: {platform.system()} ({platform.machine()}) | Port: {port}")
    print("=" * 72)
    print(f"  [SECURITY] Bound Address : {host} ({'Localhost Only' if host == '127.0.0.1' else 'LAN Network Mode'})")
    print(f"  [SECURITY] Access Token  : {auth_token}")
    print(f"  [SECURITY] Allowlist     : {len(WEB_SAFE_TOOLS)} safe + {len(WEB_CONFIRM_TOOLS)} confirmation-required tools active")
    print("-" * 72)
    print(f"  👉 Open in Browser: {access_url}")
    print("-" * 72)
    if host == "0.0.0.0":
        print("  ⚠️  WARNING: LAN mode is active. Never expose this port to the public internet!")
    print("  Press Ctrl+C to stop the server.\n")

    # Automatically open the browser on local desktop sessions
    if host == "127.0.0.1" and os.name == "nt":
        try:
            threading.Timer(0.8, lambda: webbrowser.open(access_url)).start()
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Stopping EmberOS Web Server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EmberOS Web GUI Server")
    parser.add_argument("--lan", action="store_true", help="Bind to 0.0.0.0 for LAN access from other devices")
    parser.add_argument("--host", type=str, default=None, help="Custom bind IP (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    args = parser.parse_args()

    bind_host = "0.0.0.0" if args.lan else (args.host or "127.0.0.1")
    run_server(host=bind_host, port=args.port)
