"""Focused tests for private session files and the LAN discovery contract."""

import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from emberos import web_discovery


class FakeZeroconf:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.registered = []
        self.closed = False

    def register_service(self, info):
        self.registered.append(info)

    def unregister_service(self, info):
        self.registered.remove(info)

    def close(self):
        self.closed = True


class FakeServiceInfo:
    def __init__(self, type_, name, **kwargs):
        self.type = type_
        self.name = name
        self.kwargs = kwargs


class WebDiscoveryTests(unittest.TestCase):
    def test_advertises_private_session_file_and_cleans_it_up(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_module = types.SimpleNamespace(
                IPVersion=types.SimpleNamespace(V4Only=4),
                ServiceInfo=FakeServiceInfo,
                Zeroconf=FakeZeroconf,
            )
            with (
                patch.dict(os.environ, {"EMBER_DATA_DIR": directory}),
                patch.dict(sys.modules, {"zeroconf": fake_module}),
                patch(
                    "emberos.web_discovery.psutil.net_if_addrs",
                    return_value={
                        "lo": [types.SimpleNamespace(family=socket.AF_INET, address="127.0.0.1")],
                        "eth0": [types.SimpleNamespace(family=socket.AF_INET, address="192.168.1.20")],
                    },
                ),
            ):
                advertisement = web_discovery.advertise_lan_web_session("session-secret", 8080)
                try:
                    record = json.loads(advertisement.session_file.read_text(encoding="utf-8"))
                    self.assertEqual(record["token"], "session-secret")
                    self.assertEqual(record["port"], 8080)
                    self.assertTrue(record["session_id"])
                    if os.name == "posix":
                        self.assertEqual(advertisement.session_file.stat().st_mode & 0o777, 0o600)
                    info = advertisement.service_info
                    self.assertEqual(info.type, "_emberos._tcp.local.")
                    self.assertEqual(info.kwargs["port"], 8080)
                    properties = info.kwargs["properties"]
                    self.assertEqual(properties[b"ssh_port"], b"22")
                    self.assertEqual(properties[b"session_id"].decode(), record["session_id"])
                    self.assertEqual(properties[b"token_file"].decode(), str(advertisement.session_file))
                    self.assertNotIn(b"session-secret", b"".join(properties.values()))
                finally:
                    advertisement.close()

                self.assertFalse(advertisement.session_file.exists())
                self.assertTrue(advertisement.zeroconf.closed)

    def test_rejects_invalid_port_or_empty_token(self):
        with self.assertRaises(ValueError):
            web_discovery.advertise_lan_web_session("", 8080)
        with self.assertRaises(ValueError):
            web_discovery.advertise_lan_web_session("secret", 0)


if __name__ == "__main__":
    unittest.main()
