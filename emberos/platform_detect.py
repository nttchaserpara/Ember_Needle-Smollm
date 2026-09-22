"""Runtime platform and desktop-session detection for EmberOS.

The adapters currently target Windows and Linux desktops running X11.  A
Linux machine without a usable desktop session is kept separate so desktop
tools can fail clearly instead of attempting GUI commands over SSH.
"""

import os
import platform
import subprocess


def _has_linux_desktop_session() -> bool:
    """Return whether Linux reports an active X11/Wayland login session."""
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return True

    try:
        sessions = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        session_ids = []
        for line in sessions.stdout.splitlines():
            fields = line.split()
            if fields:
                session_ids.append(fields[0])

        for session_id in session_ids:
            result = subprocess.run(
                ["loginctl", "show-session", session_id, "-p", "Type", "--value"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if result.stdout.strip().lower() in {"x11", "wayland"}:
                return True
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def detect_platform() -> str:
    """Return ``windows``, ``linux_desktop``, or ``linux_headless``."""
    system = platform.system()
    if system == "Windows":
        return "windows"
    if system == "Linux":
        return "linux_desktop" if _has_linux_desktop_session() else "linux_headless"
    # Preserve the three-state contract for unsupported environments.  The
    # Linux-headless branch produces a safe, explicit unsupported result for
    # desktop-only tools instead of attempting platform-specific commands.
    return "linux_headless"


PLATFORM = detect_platform()
IS_WINDOWS = PLATFORM == "windows"
IS_LINUX_DESKTOP = PLATFORM == "linux_desktop"
IS_LINUX_HEADLESS = PLATFORM == "linux_headless"
IS_LINUX = PLATFORM in {"linux_desktop", "linux_headless"}

