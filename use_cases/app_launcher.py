"""Application launcher for EmberOS-Windows."""

import logging
import os
import subprocess
from pathlib import Path
from emberos.outcomes import ToolOutput

from emberos.config import ROOT_DIR

logger = logging.getLogger("emberos.use_cases.app_launcher")

_LAUNCHER_LOG = ROOT_DIR / "logs" / "launcher.log"

APP_ALIASES = {
    "firefox": "firefox.exe",
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "browser": "msedge.exe",
    "vs code": "code.exe",
    "vscode": "code.exe",
    "visual studio code": "code.exe",
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "terminal": "wt.exe",
    "windows terminal": "wt.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "powershell": "powershell.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "task manager": "taskmgr.exe",
    "taskmgr": "taskmgr.exe",
    "control panel": "control.exe",
    "spotify": "Spotify.exe",
    "vlc": "vlc.exe",
    "word": "WINWORD.EXE",
    "microsoft word": "WINWORD.EXE",
    "excel": "EXCEL.EXE",
    "microsoft excel": "EXCEL.EXE",
    "powerpoint": "POWERPNT.EXE",
    "microsoft powerpoint": "POWERPNT.EXE",
    "outlook": "OUTLOOK.EXE",
    "microsoft outlook": "OUTLOOK.EXE",
    "paint": "mspaint.exe",
    "snipping tool": "SnippingTool.exe",
    "settings": "ms-settings:",
}

# Packaged Store apps that expose an execution-alias stub instead of their
# actual process. Launch their stable Start Menu AUMID directly.
KNOWN_AUMIDS = {
    "spotify": "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify",
}


def _log_launch(app: str, pid: int = 0, success: bool = True, error: str = ""):
    try:
        _LAUNCHER_LOG.parent.mkdir(parents=True, exist_ok=True)
        import time
        with open(_LAUNCHER_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | "
                    f"{'OK' if success else 'FAIL'} | {app} | PID={pid} | {error}\n")
    except Exception:
        pass


def launch_app(app_name: str) -> str:
    name_lower = app_name.lower().strip()

    # Prefer a verified packaged-app identity over an App Execution Alias
    # (for example WindowsApps\Spotify.exe), which can report success without
    # reliably bringing the actual app window to the foreground.
    aumid = KNOWN_AUMIDS.get(name_lower)
    if aumid:
        try:
            result = subprocess.run(
                ["powershell", "-NonInteractive", "-Command",
                 f'Start-Process explorer.exe -ArgumentList "shell:AppsFolder\\{aumid}" -ErrorAction Stop'],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                _log_launch(app_name, success=True)
                return f"Launching {app_name}...\n\u2713 {app_name} started (Start Menu app)"
        except Exception:
            pass  # Fall through to the standard launch tiers.

    # Check alias table
    exe = APP_ALIASES.get(name_lower)
    if exe:
        # ms-settings: and similar URI schemes
        if ":" in exe and not exe.endswith(".exe"):
            try:
                os.startfile(exe)
                _log_launch(app_name, success=True)
                return f"Launching {app_name}...\n\u2713 {app_name} opened."
            except Exception as e:
                _log_launch(app_name, success=False, error=str(e))
                return ToolOutput.failure(f"Failed to launch {app_name}: {e}")
        try:
            proc = subprocess.Popen(
                [exe], shell=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            _log_launch(app_name, pid=proc.pid)
            return f"Launching {app_name}...\n\u2713 {app_name} started successfully (PID: {proc.pid})"
        except FileNotFoundError:
            pass  # Fall through to search

    # Try finding via where
    try:
        lookup = app_name.strip()
        if not lookup.endswith(".exe"):
            lookup += ".exe"
        result = subprocess.run(
            ["where", lookup], capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            exe_path = result.stdout.strip().splitlines()[0]
            proc = subprocess.Popen(
                [exe_path], shell=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            _log_launch(app_name, pid=proc.pid)
            return f"Launching {app_name}...\n\u2713 {app_name} started successfully (PID: {proc.pid})"
    except Exception:
        pass

    # Store/UWP apps (Netflix, Sticky Notes, etc.) often do not expose a
    # launchable .exe on PATH. Ask Windows' Start Menu registry for the
    # current AppUserModelID (AUMID), then launch it through AppsFolder.
    # This is stable across package-version updates, unlike WindowsApps paths.
    try:
        script = (
            "$query = $env:EMBER_APP_LAUNCH_QUERY; "
            "$app = Get-StartApps | Where-Object { "
            "$_.Name.IndexOf($query, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 "
            "} | Select-Object -First 1; "
            "if ($app) { "
            "Start-Process explorer.exe -ArgumentList ('shell:AppsFolder\\' + $app.AppID) "
            "-ErrorAction Stop; "
            "Write-Output $app.Name "
            "}"
        )
        env = os.environ.copy()
        env["EMBER_APP_LAUNCH_QUERY"] = app_name.strip()
        result = subprocess.run(
            ["powershell", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=15, env=env,
        )
        matched_name = result.stdout.strip()
        if result.returncode == 0 and matched_name:
            _log_launch(app_name, success=True)
            return f"Launching {app_name}...\n\u2713 {matched_name} started (Start Menu app)"
    except Exception:
        pass

    # Last resort: Start-Process via PowerShell
    try:
        result = subprocess.run(
            ["powershell", "-NonInteractive", "-Command",
             f'Start-Process "{app_name}" -ErrorAction Stop'],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            _log_launch(app_name, success=True)
            return f"Launching {app_name}...\n\u2713 {app_name} started (via PowerShell)"
        _log_launch(app_name, success=False, error=result.stderr.strip()[:200])
        return (
            ToolOutput.failure(f"Could not launch '{app_name}' -- it's not a recognized alias, "
            f"not on PATH, and not found in your Start Menu apps either. "
            f"Double check the name (e.g. exact spelling as it appears in Start Menu).")
        )
    except Exception as e:
        _log_launch(app_name, success=False, error=str(e))
        return ToolOutput.failure(f"Could not launch {app_name}: {e}")


def open_file_with_default_app(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ToolOutput.failure(f"File not found: {path}")
    os.startfile(str(p))
    return f"Opening {p.name}..."


def launch_app_from_path(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ToolOutput.failure(f"Not found: {path}")
    try:
        proc = subprocess.Popen([str(p)])
        _log_launch(path, pid=proc.pid)
        return f"Launched: {p.name} (PID: {proc.pid})"
    except Exception as e:
        return ToolOutput.failure(f"Failed to launch {path}: {e}")
