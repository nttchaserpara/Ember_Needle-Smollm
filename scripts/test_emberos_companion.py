"""Platform-neutral tests for the companion's mDNS discovery contract."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop" / "companion"))

from emberos_connect import (
    _app_config_dir,
    _app_browser,
    _desktop_exec_arg,
    _device_from_service_info,
    _linux_autostart_file,
)


class FakeServiceInfo:
    def __init__(self, properties, port=8080, addresses=None):
        self.properties = properties
        self.port = port
        self.addresses = addresses or ["192.168.1.50"]

    def parsed_addresses(self, _version):
        return self.addresses


class CompanionDiscoveryTests(unittest.TestCase):
    def test_parses_only_supported_discovery_records(self):
        record = FakeServiceInfo(
            {
                b"device_id": b"d0c23b25-f100-4cb3-8080-5d4969439e11",
                b"device_name": b"emberos-zero2w",
                b"ssh_port": b"22",
                b"token_file": b"/home/natta/.local/share/emberos/runtime/web-session-42.json",
                b"session_id": b"session-abc",
                b"protocol": b"1",
            }
        )

        device = _device_from_service_info(record, "EmberOS._emberos._tcp.local.")

        self.assertEqual(device.name, "emberos-zero2w")
        self.assertEqual(device.addresses, ("192.168.1.50",))
        self.assertEqual(device.ssh_port, 22)
        self.assertEqual(device.web_port, 8080)
        self.assertEqual(device.token_file, "/home/natta/.local/share/emberos/runtime/web-session-42.json")
        self.assertEqual(device.session_id, "session-abc")

    def test_rejects_unsupported_protocol_and_missing_address(self):
        record = FakeServiceInfo({
            b"device_id": b"pi", b"token_file": b"/session", b"session_id": b"session",
            b"protocol": b"2",
        })
        self.assertIsNone(_device_from_service_info(record, "EmberOS._emberos._tcp.local."))

        record.properties[b"protocol"] = b"1"
        record.addresses = []
        self.assertIsNone(_device_from_service_info(record, "EmberOS._emberos._tcp.local."))

    def test_config_and_autostart_paths_follow_linux_desktop_conventions(self):
        self.assertEqual(
            _app_config_dir(
                system="Linux",
                environ={"XDG_CONFIG_HOME": "/tmp/user-config"},
                home="/home/tester",
            ),
            Path("/tmp/user-config/emberos"),
        )
        self.assertEqual(
            _app_config_dir(system="Windows", environ={}, home="C:/Users/tester"),
            Path("C:/Users/tester/AppData/Local/EmberOS"),
        )
        self.assertEqual(
            _linux_autostart_file(
                environ={"XDG_CONFIG_HOME": "/tmp/user-config"}, home="/home/tester"
            ),
            Path("/tmp/user-config/autostart/emberos-connect.desktop"),
        )

    @patch("emberos_connect.shutil.which")
    def test_linux_chooses_an_installed_app_browser(self, which):
        which.side_effect = lambda name: "/usr/bin/chromium" if name == "chromium" else None
        self.assertEqual(_app_browser(system="Linux"), "/usr/bin/chromium")
        which.assert_called_once_with("chromium")

    def test_desktop_exec_argument_escapes_desktop_entry_metacharacters(self):
        self.assertEqual(_desktop_exec_arg('A "quoted" 100%'), '"A \\"quoted\\" 100%%"')


if __name__ == "__main__":
    unittest.main()
