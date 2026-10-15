"""Platform-neutral tests for the companion's mDNS discovery contract."""

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop" / "companion"))

from emberos_connect import _device_from_service_info


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


if __name__ == "__main__":
    unittest.main()
