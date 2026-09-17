"""
web_ember.py

Lightweight, secure, zero-dependency Web GUI for EmberOS.
Theme: Dark & Orange Chatroom Interface with clean responses and translucent
collapsible benchmark dropdowns under every assistant turn.

Security Controls:
  - Token-based Authentication (X-Ember-Token header)
  - Web-Safe Tool Allowlist (WEB_SAFE_TOOLS) - blocks RCE & destructive tools
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
    # System monitoring & queries (read-only)
    "disk_usage", "ram_status", "running_processes", "system_uptime",
    "cpu_info", "cpu_temperature", "battery_status", "get_open_windows",
    "get_volume", "get_brightness", "list_tasks", "get_file_info",
    "find_files", "find_large_files", "find_old_files", "find_duplicate_files",
    "get_image_info", "list_archive_contents", "search_notes",
    "search_conversation_history", "folder_explain",
    # Safe reversible system controls
    "set_volume", "volume_up", "volume_down", "mute_volume",
    "set_brightness", "toggle_dark_mode", "set_dark_mode",
    # Task management
    "add_task", "complete_task", "remove_task",
    # Safe document summary
    "summarize_file",
    # Undo
    "undo_last_action",
}

# Explicitly blocked tools for Web API (even if token is valid)
BLOCKED_TOOLS = {
    "run_shell", "delete_file", "kill_process", "shutdown_system",
    "restart_system", "search_web", "open_file", "write_file",
    "sleep_system", "lock_screen", "cancel_shutdown", "rename_file",
    "move_file", "organize_folder", "clear_completed_tasks",
    "create_note", "create_spreadsheet", "create_google_doc",
    "write_document", "extract_audio", "extract_video_clip", "batch_read_folder",
}

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
# Security: web-safe ToolRegistry -- refuses any tool outside WEB_SAFE_TOOLS
# BEFORE it reaches tool.func. Covers both execution paths in tools.py:
# execute_tool() (single call) and execute_tool_chain() (multi-step), which
# are independent code paths -- overriding only one leaves the other open.
# ---------------------------------------------------------------------------
class WebSafeToolRegistry(ToolRegistry):
    def execute_tool(self, name: str, params: dict):
        if name not in WEB_SAFE_TOOLS:
            return ToolResult(
                success=False,
                error=f"'{name}' is restricted in Web mode.",
                status="unsupported",
            )
        return super().execute_tool(name, params)

    def execute_tool_chain(self, calls: list[dict]):
        # All-or-nothing: if ANY step in the chain is outside the allowlist,
        # refuse the whole chain before any step runs -- don't execute the
        # allowed steps that happen to come before the blocked one.
        if any(c.get("name") not in WEB_SAFE_TOOLS for c in calls):
            results = []
            for c in calls:
                name, params = c.get("name"), c.get("arguments") or {}
                if name not in WEB_SAFE_TOOLS:
                    blocked = ToolResult(
                        success=False,
                        error=f"'{name}' is restricted in Web mode.",
                        status="unsupported",
                    )
                    results.append({"route": "tool_call", "tool": name,
                                    "arguments": params, **blocked.to_dict()})
                else:
                    results.append({"route": "skipped", "tool": name, "arguments": params})
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
  <title>EmberOS Agent</title>
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
      padding: 12px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      box-shadow: 0 4px 20px rgba(0,0,0,0.4);
      z-index: 10;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .brand-icon {
      width: 28px;
      height: 28px;
      background: linear-gradient(135deg, #ff6b35, #ff9f43);
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 0 12px var(--orange-glow);
    }
    .brand-icon svg { width: 16px; height: 16px; fill: white; }
    .brand-title {
      font-size: 1.15rem;
      font-weight: 700;
      letter-spacing: -0.5px;
      background: linear-gradient(90deg, #ffffff, #ff9f43);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    .header-badges {
      display: flex;
      align-items: center;
      gap: 12px;
      font-size: 0.75rem;
    }
    .badge {
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      padding: 4px 10px;
      border-radius: 20px;
      color: var(--text-muted);
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .badge .dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--success);
      box-shadow: 0 0 6px var(--success);
    }
    .badge.token-badge {
      border-color: var(--orange-dim);
      color: var(--orange-hover);
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
      font-family: Consolas, monospace;
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
      font-family: Consolas, Monaco, monospace;
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

    /* Quick Chips */
    .chips-bar {
      max-width: 820px;
      width: 100%;
      margin: 0 auto;
      padding: 0 20px 8px 20px;
      display: flex;
      gap: 8px;
      overflow-x: auto;
      white-space: nowrap;
    }
    .chip {
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      color: var(--text-muted);
      font-size: 0.75rem;
      padding: 5px 12px;
      border-radius: 16px;
      cursor: pointer;
      transition: all 0.15s;
    }
    .chip:hover {
      border-color: var(--orange-primary);
      color: var(--orange-hover);
      background: rgba(255, 107, 53, 0.08);
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
      transition: border-color 0.2s, box-shadow 0.2s;
    }
    .input-box-wrapper:focus-within {
      border-color: var(--orange-primary);
      box-shadow: 0 0 0 3px var(--orange-glow);
    }
    textarea#user-input {
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
    button#send-btn {
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
    button#send-btn:hover:not(:disabled) {
      background: linear-gradient(135deg, var(--orange-hover), var(--orange-primary));
      box-shadow: 0 0 14px var(--orange-glow);
      transform: translateY(-1px);
    }
    button#send-btn:disabled {
      opacity: 0.4;
      cursor: not-allowed;
    }
  </style>
</head>
<body>

  <header>
    <div class="brand">
      <div class="brand-icon">
        <svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/></svg>
      </div>
      <div class="brand-title">EmberOS Agent</div>
    </div>
    <div class="header-badges">
      <div class="badge"><span class="dot"></span> <span id="platform-text">Connected</span></div>
      <div class="badge token-badge" id="token-status" title="Auth Token Active">🔒 Token Secured</div>
    </div>
  </header>

  <main id="chat-container">
    <div class="msg-wrapper msg-ember">
      <div class="bubble">
        👋 <strong>Welcome to EmberOS Web Terminal!</strong><br>
        Ask system commands, inspect resources, or manage tasks. Your request executes securely on this machine.
      </div>
    </div>
  </main>

  <div class="chips-bar">
    <div class="chip" onclick="sendPrompt('show ram status and disk space')">📊 RAM & Disk</div>
    <div class="chip" onclick="sendPrompt('what is my system uptime?')">⏱️ Uptime</div>
    <div class="chip" onclick="sendPrompt('show my tasks')">📝 Tasks</div>
    <div class="chip" onclick="sendPrompt('Summarize &quot;experiments/fixtures/sample-summary.txt&quot;')">📄 Summarize Sample</div>
    <div class="chip" onclick="sendPrompt('/memory')">🧠 Memory</div>
  </div>

  <footer>
    <form class="input-form" id="chat-form" onsubmit="event.preventDefault(); handleSend();">
      <div class="input-box-wrapper">
        <textarea id="user-input" rows="1" placeholder="Type a command or question (e.g. 'show disk space')..." onkeydown="handleKeyDown(event)"></textarea>
      </div>
      <button type="submit" id="send-btn">Send</button>
    </form>
  </footer>

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
    const userInput = document.getElementById('user-input');
    const sendBtn = document.getElementById('send-btn');

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
            <span class="stat-label">Execution Time</span>
            <span class="stat-val">${meta.elapsed_seconds ? meta.elapsed_seconds.toFixed(3) + 's' : 'N/A'}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">Confidence Score</span>
            <span class="stat-val">${meta.confidence !== null ? meta.confidence.toFixed(4) : 'None'}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">Target Route</span>
            <span class="stat-val">${escapeHtml(meta.route || 'N/A')}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">Tool / Steps</span>
            <span class="stat-val">${escapeHtml(meta.tool || 'N/A')}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">Ember Process RSS</span>
            <span class="stat-val">${meta.rss_current_mb || '0'} MB (delta: ${meta.rss_delta_mb || '0'} MB)</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">Peak Total RSS</span>
            <span class="stat-val">${meta.peak_total_mib || '0'} MiB (Children: ${meta.peak_children_mib || '0'} MiB)</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">System RAM Used</span>
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

    async function handleSend() {
      const query = userInput.value.trim();
      if (!query) return;

      appendMessage('user', query);
      userInput.value = '';
      userInput.style.height = 'auto';

      sendBtn.disabled = true;
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

        if (response.status === 401) {
          appendMessage('ember', '❌ <strong>Authentication Failed</strong>: Invalid or missing token. Please reload and enter a valid access token.');
          return;
        }

        if (response.status === 403) {
          const errData = await response.json();
          appendMessage('ember', `⚠️ <strong>Security Block</strong>: ${errData.error}`);
          return;
        }

        if (!response.ok) {
          const errText = await response.text();
          appendMessage('ember', `❌ <strong>Request Error (${response.status})</strong>: ${escapeHtml(errText)}`);
          return;
        }

        const data = await response.json();
        appendMessage('ember', data.answer, data.meta);

      } catch (err) {
        if (typingEl) typingEl.remove();
        appendMessage('ember', `❌ <strong>Network / Connection Error</strong>: ${escapeHtml(err.message)}`);
      } finally {
        sendBtn.disabled = false;
        userInput.focus();
      }
    }

    function handleKeyDown(event) {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        handleSend();
      }
    }

    function sendPrompt(text) {
      userInput.value = text;
      handleSend();
    }

    // Auto resize textarea
    userInput.addEventListener('input', function() {
      this.style.height = 'auto';
      this.style.height = (this.scrollHeight) + 'px';
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
                # Pre-flight Tool Routing Check for Web-Safe Allowlist
                # We let handle_request execute only if tool is in WEB_SAFE_TOOLS.
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

        # Check if executed tool was blocked/disallowed in Web mode
        tool_name = result.get("tool") or ""
        called_tools = [tool_name]
        if result.get("route") == "multi_tool_call":
            called_tools = [item.get("tool") for item in result.get("results", []) if item.get("tool")]

        # Block any result containing blocked tools
        is_blocked = any(t in BLOCKED_TOOLS or (t and t not in WEB_SAFE_TOOLS) for t in called_tools)
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
    print(f"  [SECURITY] Allowlist     : {len(WEB_SAFE_TOOLS)} web-safe tools active (destructive tools blocked)")
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

