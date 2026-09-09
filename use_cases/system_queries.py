"""System query functions for EmberOS-Windows."""

import ctypes
import logging
from emberos.outcomes import ToolOutput
import subprocess
from datetime import datetime, timezone

import psutil

logger = logging.getLogger("emberos.use_cases.system_queries")


def get_disk_usage() -> str:
    lines = ["Drive    Size   Used   Free   Use%"]
    lines.append("-" * 42)
    main_free = ""
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except PermissionError:
            continue
        total = usage.total / (1024 ** 3)
        used = usage.used / (1024 ** 3)
        free = usage.free / (1024 ** 3)

        def _fmt(gb):
            if gb >= 1000:
                return f"{gb / 1024:.1f}T"
            return f"{gb:.0f}G"

        drive = part.mountpoint
        lines.append(f"{drive:<9}{_fmt(total):>5}  {_fmt(used):>5}  {_fmt(free):>5}   {usage.percent:.0f}%")
        if drive.upper().startswith("C"):
            main_free = f"\nYour main drive (C:) has {free:.0f}GB available."
    return "\n".join(lines) + main_free


def get_ram_status() -> str:
    vm = psutil.virtual_memory()
    total = vm.total / (1024 ** 3)
    used = vm.used / (1024 ** 3)
    avail = vm.available / (1024 ** 3)
    return (f"RAM: {used:.1f}GB used of {total:.1f}GB total ({vm.percent}% used). "
            f"{avail:.1f}GB available.")


def get_running_processes(filter_name: str = None) -> str:
    procs = []
    for p in psutil.process_iter(["name", "pid", "cpu_percent", "memory_percent"]):
        try:
            info = p.info
            if filter_name and filter_name.lower() not in (info["name"] or "").lower():
                continue
            procs.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    procs.sort(key=lambda x: x.get("cpu_percent") or 0, reverse=True)
    procs = procs[:15]

    lines = [f"{'PID':<8} {'Name':<30} {'CPU%':<8} {'RAM%':<8}"]
    lines.append("-" * 54)
    for p in procs:
        lines.append(
            f"{p['pid']:<8} {(p['name'] or '?'):<30} "
            f"{(p['cpu_percent'] or 0):<8.1f} {(p['memory_percent'] or 0):<8.1f}"
        )
    return "\n".join(lines)


def get_system_uptime() -> str:
    boot = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc)
    delta = datetime.now(timezone.utc) - boot
    days = delta.days
    hours = delta.seconds // 3600
    minutes = (delta.seconds % 3600) // 60
    return f"System uptime: {days}d {hours}h {minutes}m"


def get_cpu_info() -> str:
    cpu_name = "Unknown"
    try:
        result = subprocess.run(
            ["wmic", "cpu", "get", "name"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line and line.lower() != "name":
                cpu_name = line
                break
    except Exception:
        pass

    freq = psutil.cpu_freq()
    per_cpu = psutil.cpu_percent(interval=1, percpu=True)
    cores = psutil.cpu_count(logical=False) or "?"
    threads = psutil.cpu_count(logical=True) or "?"
    current_freq = f"{freq.current:.0f}MHz" if freq else "N/A"

    lines = [
        f"CPU: {cpu_name}",
        f"Cores: {cores} physical, {threads} logical",
        f"Frequency: {current_freq}",
        f"Per-core usage: {', '.join(f'{p:.0f}%' for p in per_cpu)}",
        f"Average: {sum(per_cpu) / len(per_cpu):.1f}%",
    ]
    return "\n".join(lines)


def check_cpu_temperature() -> str:
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            lines = []
            for name, entries in temps.items():
                for entry in entries:
                    label = entry.label or name
                    lines.append(f"{label}: {entry.current:.1f}°C")
            if lines:
                return "\n".join(lines)
    except (AttributeError, Exception):
        pass
    return (ToolOutput.failure("CPU temperature monitoring is not available on this system. "
            "Use HWMonitor or HWiNFO for temperature readings."))


def get_battery_status() -> str:
    battery = psutil.sensors_battery()
    if battery is None:
        return ToolOutput.failure("No battery detected — this system is likely a desktop.")
    percent = battery.percent
    plugged = battery.power_plugged
    if plugged:
        status = "Plugged in, charging" if percent < 100 else "Plugged in, fully charged"
    else:
        secs = battery.secsleft
        if secs == psutil.POWER_TIME_UNLIMITED or secs < 0:
            remain = ""
        else:
            h, m = divmod(secs // 60, 60)
            remain = f" (~{h}h {m}m remaining)"
        status = f"On battery{remain}"
    return f"Battery: {percent:.0f}% — {status}"


def lock_screen() -> str:
    try:
        ctypes.windll.user32.LockWorkStation()
        return "Screen locked."
    except Exception as e:
        return ToolOutput.failure(f"Could not lock screen: {e}")


def sleep_system() -> str:
    try:
        subprocess.Popen(
            ["rundll32.exe", "powrprof.dll,SetSuspendState", "0", "1", "0"]
        )
        return "Putting system to sleep…"
    except Exception as e:
        return ToolOutput.failure(f"Could not sleep: {e}")


def shutdown_system(delay_seconds: int = 0) -> str:
    try:
        subprocess.run(
            ["shutdown", "/s", "/t", str(delay_seconds)],
            check=True, capture_output=True,
        )
        msg = "Shutting down now." if delay_seconds == 0 else f"Shutdown scheduled in {delay_seconds}s."
        return msg
    except Exception as e:
        return ToolOutput.failure(f"Shutdown failed: {e}")


def restart_system(delay_seconds: int = 0) -> str:
    try:
        subprocess.run(
            ["shutdown", "/r", "/t", str(delay_seconds)],
            check=True, capture_output=True,
        )
        msg = "Restarting now." if delay_seconds == 0 else f"Restart scheduled in {delay_seconds}s."
        return msg
    except Exception as e:
        return ToolOutput.failure(f"Restart failed: {e}")


def cancel_shutdown() -> str:
    try:
        subprocess.run(["shutdown", "/a"], check=True, capture_output=True)
        return "Scheduled shutdown/restart cancelled."
    except Exception as e:
        return ToolOutput.failure(f"Cancel failed (no shutdown pending?): {e}")


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------

_VK_VOLUME_MUTE = 0xAD
_VK_VOLUME_DOWN = 0xAE
_VK_VOLUME_UP   = 0xAF
_KEYEVENTF_KEYUP = 0x0002


def _send_key(vk: int):
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)


def volume_up(steps: int = 2) -> str:
    for _ in range(max(1, steps)):
        _send_key(_VK_VOLUME_UP)
    return f"Volume increased ({steps} step{'s' if steps != 1 else ''})."


def volume_down(steps: int = 2) -> str:
    for _ in range(max(1, steps)):
        _send_key(_VK_VOLUME_DOWN)
    return f"Volume decreased ({steps} step{'s' if steps != 1 else ''})."


def mute_volume() -> str:
    _send_key(_VK_VOLUME_MUTE)
    return "Volume toggled mute."


def _master_volume(level: int = None) -> tuple[int, bool]:
    """Read or set the default audio endpoint using Windows Core Audio."""
    import comtypes
    from pycaw.pycaw import AudioUtilities

    # Registry calls may run on worker threads, each with its own COM lifetime.
    comtypes.CoInitialize()
    device = endpoint = None
    try:
        device = AudioUtilities.GetSpeakers()
        if device is None:
            raise RuntimeError("No default audio output device is available")
        endpoint = device.EndpointVolume
        if level is not None:
            endpoint.SetMasterVolumeLevelScalar(level / 100.0, None)
        actual = round(endpoint.GetMasterVolumeLevelScalar() * 100)
        muted = bool(endpoint.GetMute())
        return actual, muted
    finally:
        # Release interfaces before balancing this thread's COM initialization.
        endpoint = device = None
        comtypes.CoUninitialize()


def set_volume(level: int) -> str:
    """Set the absolute master audio volume percentage and verify the result."""
    if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 100:
        raise ValueError("Volume level must be an integer between 0 and 100")
    actual, muted = _master_volume(level)
    if actual != level:
        raise RuntimeError(f"Requested volume {level}%, but the audio device reports {actual}%")
    suffix = " Audio is still muted." if muted else ""
    return ToolOutput(f"Volume set to {actual}%.{suffix}",
                      message=f"Done! Your volume is now {actual}%.{suffix}",
                      data={"level": actual, "muted": muted})


def get_volume() -> str:
    """Read the master audio volume percentage and mute state."""
    actual, muted = _master_volume()
    suffix = " (muted)" if muted else ""
    return f"Current volume: {actual}%{suffix}"


# ---------------------------------------------------------------------------
# Brightness
# ---------------------------------------------------------------------------

def get_brightness() -> str:
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness"],
            capture_output=True, text=True, timeout=10,
        )
        val = result.stdout.strip()
        if val.isdigit():
            return f"Current brightness: {val}%"
    except Exception:
        pass
    return ToolOutput.failure("Could not read brightness (may not be supported on this display).")


def set_brightness(level: int) -> str:
    if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 100:
        raise ValueError("Brightness level must be an integer between 0 and 100")
    try:
        script = (
            "$ErrorActionPreference = 'Stop'; "
            "$monitors = @(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods); "
            "if ($monitors.Count -eq 0) { throw 'No controllable display found' }; "
            "foreach ($monitor in $monitors) { "
            "$change = Invoke-CimMethod -InputObject $monitor -MethodName WmiSetBrightness "
            f"-Arguments @{{Timeout=[uint32]1; Brightness=[byte]{level}}}; "
            "if ($change.ReturnValue -ne 0) { throw 'Display rejected brightness change' } }; "
            "$levels = @(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness | "
            "Select-Object -ExpandProperty CurrentBrightness); "
            "@{levels=$levels} | ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            ["powershell", "-Command", script],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return ToolOutput.failure(f"Brightness change failed: {result.stderr.strip()[:200]}")
        import json
        actual = json.loads(result.stdout).get("levels")
        if not isinstance(actual, list) or not actual or any(type(value) is not int or value != level for value in actual):
            return ToolOutput.failure(f"The brightness command ran, but I couldn't verify {level}% on all displays.",
                                      status="partial", data={"reported_levels": actual})
        return ToolOutput(f"Brightness set to {actual[0]}%.",
                          message=f"Done! Your screen brightness is now {actual[0]}%.",
                          data={"level": actual[0], "reported_levels": actual})
    except Exception as e:
        return ToolOutput.failure(f"Could not set brightness: {e}")


# ---------------------------------------------------------------------------
# Dark / Light mode
# ---------------------------------------------------------------------------

def toggle_dark_mode() -> str:
    """Toggle Windows dark/light mode by flipping the registry key."""
    reg_path = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    script = f"""
$val = (Get-ItemProperty -Path '{reg_path}' -Name AppsUseLightTheme).AppsUseLightTheme
$new = if ($val -eq 1) {{0}} else {{1}}
Set-ItemProperty -Path '{reg_path}' -Name AppsUseLightTheme -Value $new
Set-ItemProperty -Path '{reg_path}' -Name SystemUsesLightTheme -Value $new
if ($new -eq 0) {{ 'dark' }} else {{ 'light' }}
"""
    try:
        result = subprocess.run(
            ["powershell", "-Command", script],
            capture_output=True, text=True, timeout=10,
        )
        mode = result.stdout.strip()
        if mode in ("dark", "light"):
            return f"Switched to {mode} mode."
        return ToolOutput.failure(f"Dark mode toggle may have failed: {result.stderr.strip()[:200]}")
    except Exception as e:
        return ToolOutput.failure(f"Could not toggle dark mode: {e}")


def set_dark_mode(enable: bool) -> str:
    """Explicitly enable or disable dark mode."""
    val = 0 if enable else 1
    reg_path = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    script = f"""
Set-ItemProperty -Path '{reg_path}' -Name AppsUseLightTheme -Value {val}
Set-ItemProperty -Path '{reg_path}' -Name SystemUsesLightTheme -Value {val}
"""
    try:
        result = subprocess.run(
            ["powershell", "-Command", script],
            capture_output=True, text=True, timeout=10,
        )
        mode = "dark" if enable else "light"
        if result.returncode == 0:
            return f"{mode.capitalize()} mode enabled."
        return ToolOutput.failure(f"Mode change failed: {result.stderr.strip()[:200]}")
    except Exception as e:
        return ToolOutput.failure(f"Could not set mode: {e}")


# ---------------------------------------------------------------------------
# Window management
# ---------------------------------------------------------------------------

def get_open_windows() -> str:
    try:
        import pygetwindow as gw
        wins = [w for w in gw.getAllWindows() if w.title.strip()]
        if not wins:
            return "No visible windows found."
        lines = [f"  [{i+1}] {w.title}" for i, w in enumerate(wins[:30])]
        return "Open windows:\n" + "\n".join(lines)
    except ImportError:
        return ToolOutput.failure("pygetwindow not available.")
    except Exception as e:
        return ToolOutput.failure(f"Could not list windows: {e}")


def minimize_all_windows() -> str:
    try:
        # Win+D shows desktop / minimizes all
        ctypes.windll.user32.keybd_event(0x5B, 0, 0, 0)        # Win down
        ctypes.windll.user32.keybd_event(0x44, 0, 0, 0)        # D down
        ctypes.windll.user32.keybd_event(0x44, 0, _KEYEVENTF_KEYUP, 0)
        ctypes.windll.user32.keybd_event(0x5B, 0, _KEYEVENTF_KEYUP, 0)
        return "All windows minimized (Show Desktop)."
    except Exception as e:
        return ToolOutput.failure(f"Could not minimize windows: {e}")


def focus_window(title_fragment: str) -> str:
    try:
        import pygetwindow as gw
        matches = [w for w in gw.getAllWindows()
                   if title_fragment.lower() in w.title.lower()]
        if not matches:
            return ToolOutput.failure(f"No window found matching '{title_fragment}'.")
        win = matches[0]
        win.restore()
        win.activate()
        return f"Focused: {win.title}"
    except ImportError:
        return ToolOutput.failure("pygetwindow not available.")
    except Exception as e:
        return ToolOutput.failure(f"Could not focus window: {e}")
