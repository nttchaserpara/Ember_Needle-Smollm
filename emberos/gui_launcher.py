"""Cross-platform launcher for EmberOS's local web GUI."""

import os
from pathlib import Path
import platform
import shutil
import subprocess
import webbrowser


def _app_browser(system: str, environ: dict[str, str]) -> str | None:
    if system == "Windows":
        for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            root = environ.get(variable)
            if root:
                candidate = Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
                if candidate.is_file():
                    return str(candidate)
        return shutil.which("msedge")
    if system == "Linux":
        for name in (
            "chromium", "chromium-browser", "google-chrome-stable", "google-chrome",
            "microsoft-edge-stable", "microsoft-edge", "brave-browser",
        ):
            browser = shutil.which(name)
            if browser:
                return browser
    return None


def open_gui_window(url: str) -> bool:
    """Open an app-style Chromium window, falling back to the default browser."""
    system = platform.system()
    browser = _app_browser(system, os.environ)
    if browser:
        try:
            command = [browser, f"--app={url}", "--no-first-run", "--no-default-browser-check"]
            options = {}
            if system == "Windows":
                options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            else:
                options["start_new_session"] = True
                options["stdout"] = subprocess.DEVNULL
                options["stderr"] = subprocess.DEVNULL
            subprocess.Popen(command, **options)
            print(f"[GUI] Opened EmberOS in an app window with {Path(browser).name}.")
            return True
        except OSError as exc:
            print(f"[GUI] Could not launch app window: {exc}")

    try:
        opened = webbrowser.open_new(url)
        if opened:
            print("[GUI] Opened EmberOS in the default browser.")
        return bool(opened)
    except Exception as exc:
        print(f"[GUI] Could not open a browser: {exc}")
        return False
