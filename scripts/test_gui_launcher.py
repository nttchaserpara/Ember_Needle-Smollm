"""Test app-window browser selection and fallback for desktop GUI launch."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from emberos import gui_launcher


class GuiLauncherTests(unittest.TestCase):
    @patch.object(gui_launcher.subprocess, "Popen")
    @patch.object(gui_launcher.shutil, "which")
    @patch.object(gui_launcher.platform, "system", return_value="Linux")
    def test_linux_uses_chromium_app_mode(self, _system, which, popen):
        which.side_effect = lambda name: "/usr/bin/chromium" if name == "chromium" else None

        self.assertTrue(gui_launcher.open_gui_window("http://localhost:8080/?token=test"))

        command = popen.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/chromium")
        self.assertIn("--app=http://localhost:8080/?token=test", command)
        self.assertIn("--no-first-run", command)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    @patch.object(gui_launcher.webbrowser, "open_new", return_value=True)
    @patch.object(gui_launcher.shutil, "which", return_value=None)
    @patch.object(gui_launcher.platform, "system", return_value="Linux")
    def test_linux_falls_back_to_default_browser(self, _system, _which, open_new):
        url = "http://localhost:8080/?token=test"
        self.assertTrue(gui_launcher.open_gui_window(url))
        open_new.assert_called_once_with(url)


if __name__ == "__main__":
    unittest.main()
