"""Mocked X11/Linux adapter checks; no desktop commands are executed."""

import os
from pathlib import Path
import sys
from subprocess import CompletedProcess
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from emberos.outcomes import ToolOutput
from use_cases import app_launcher, media_ops, system_queries


def test_x11_brightness_and_volume_commands():
    original = (system_queries.IS_LINUX_DESKTOP, system_queries.IS_LINUX,
                system_queries._IS_WINDOWS)
    system_queries.IS_LINUX_DESKTOP = True
    system_queries.IS_LINUX = True
    system_queries._IS_WINDOWS = False
    try:
        def fake_run(command, **kwargs):
            if command == ["xrandr", "--query"]:
                return CompletedProcess(command, 0,
                                        "HDMI-1 connected primary 1920x1080\n"
                                        "DP-1 connected 1280x720\n", "")
            if command == ["xrandr", "--verbose"]:
                return CompletedProcess(command, 0,
                                        "HDMI-1 connected primary 1920x1080\n"
                                        "\tBrightness: 0.75\n"
                                        "DP-1 connected 1280x720\n"
                                        "\tBrightness: 0.50\n", "")
            if command and command[0] == "xrandr":
                return CompletedProcess(command, 0, "", "")
            if command[:2] == ["pactl", "get-sink-volume"]:
                return CompletedProcess(command, 0, "Volume: front-left: 49152 / 75%\n", "")
            if command[:2] == ["pactl", "get-sink-mute"]:
                return CompletedProcess(command, 0, "Mute: no\n", "")
            return CompletedProcess(command, 0, "", "")

        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False), \
             patch.object(system_queries.subprocess, "run", side_effect=fake_run):
            brightness = system_queries.get_brightness()
            assert "HDMI-1: 75%" in brightness and "DP-1: 50%" in brightness
            system_queries.set_brightness(80)
            volume = system_queries.get_volume()
            assert volume == "Current volume: 75%"
    finally:
        (system_queries.IS_LINUX_DESKTOP,
         system_queries.IS_LINUX,
         system_queries._IS_WINDOWS) = original


def test_linux_launcher_and_screenshot():
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "screen.png"

        with patch.object(app_launcher, "IS_LINUX_DESKTOP", True), \
             patch.object(app_launcher, "IS_WINDOWS", False), \
             patch.object(app_launcher.shutil, "which", return_value="/usr/bin/firefox"), \
             patch.object(app_launcher.subprocess, "Popen") as popen:
            popen.return_value.pid = 42
            result = app_launcher.launch_app("firefox")
            assert "PID: 42" in result

        def screenshot_run(command, **kwargs):
            Path(command[1]).touch()
            return CompletedProcess(command, 0, "", "")

        with patch.object(media_ops, "IS_LINUX_DESKTOP", True), \
             patch.object(media_ops, "IS_WINDOWS", False), \
             patch.object(media_ops.shutil, "which",
                          side_effect=lambda name: "/usr/bin/scrot" if name == "scrot" else None), \
             patch.object(media_ops.subprocess, "run", side_effect=screenshot_run):
            result = media_ops.take_screenshot(str(output))
            assert output.exists() and str(output) in result


if __name__ == "__main__":
    test_x11_brightness_and_volume_commands()
    test_linux_launcher_and_screenshot()
    print("PASS: mocked Linux X11 adapters")

