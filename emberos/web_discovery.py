"""LAN discovery and short-lived credentials for the desktop companion."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import tempfile
from uuid import uuid4

import psutil

from emberos.config import ROOT_DIR, data_dir


SERVICE_TYPE = "_emberos._tcp.local."
DEVICE_ID_FILENAME = "device-id"


def _runtime_dir() -> Path:
    directory = data_dir(ROOT_DIR) / "runtime"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    return directory


def _device_id(directory: Path) -> str:
    path = directory / DEVICE_ID_FILENAME
    try:
        value = path.read_text(encoding="ascii").strip()
        if re.fullmatch(r"[0-9a-f-]{36}", value):
            return value
    except OSError:
        pass

    value = str(uuid4())
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="ascii", dir=directory, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(value + "\n")
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return value


def _lan_addresses() -> list[bytes]:
    found: list[tuple[int, bytes]] = []
    for entries in psutil.net_if_addrs().values():
        for entry in entries:
            if entry.family != socket.AF_INET:
                continue
            try:
                address = ipaddress.IPv4Address(entry.address)
            except ipaddress.AddressValueError:
                continue
            if address.is_loopback or address.is_link_local or address.is_unspecified:
                continue
            priority = 0 if address.is_private else 1
            packed = socket.inet_aton(str(address))
            if all(existing != packed for _, existing in found):
                found.append((priority, packed))
    return [packed for _, packed in sorted(found, key=lambda item: item[0])]


@dataclass
class LanWebAdvertisement:
    """Own the mDNS registration and private session file for one server."""

    zeroconf: object
    service_info: object
    session_file: Path
    process_id: int
    device_id: str
    session_id: str

    def close(self) -> None:
        try:
            self.zeroconf.unregister_service(self.service_info)
        except Exception:
            pass
        try:
            self.zeroconf.close()
        except Exception:
            pass
        try:
            saved = json.loads(self.session_file.read_text(encoding="utf-8"))
            if saved.get("pid") == self.process_id and saved.get("session_id") == self.session_id:
                self.session_file.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            pass


def advertise_lan_web_session(token: str, port: int) -> LanWebAdvertisement:
    """Advertise an EmberOS server to paired clients on its local network.

    The authentication token is only written to a mode-0600 file under the
    current user's mode-0700 runtime directory. Discovery advertises that
    file's path, never the token itself; clients fetch it over SSH.
    """
    if not token or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("A valid web token and TCP port are required")

    from zeroconf import IPVersion, ServiceInfo, Zeroconf

    runtime = _runtime_dir()
    device_id = _device_id(runtime)
    session_id = str(uuid4())
    session_file = runtime / f"web-session-{os.getpid()}.json"
    record = {"token": token, "port": port, "pid": os.getpid(), "session_id": session_id}

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=runtime, delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(record, handle)
            handle.write("\n")
        temporary.chmod(0o600)
        os.replace(temporary, session_file)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()

    addresses = _lan_addresses()
    if not addresses:
        session_file.unlink(missing_ok=True)
        raise RuntimeError("No LAN IPv4 address is available for EmberOS discovery")

    host = re.sub(r"[^A-Za-z0-9-]", "-", socket.gethostname()).strip("-").lower()
    host = host or "emberos"
    service_name = f"EmberOS-{device_id[:8]}._emberos._tcp.local."
    server_name = f"{host}.local."
    properties = {
        b"device_id": device_id.encode("ascii"),
        b"device_name": socket.gethostname().encode("utf-8", errors="replace")[:120],
        b"ssh_host": socket.gethostname().encode("utf-8", errors="replace")[:120],
        b"ssh_port": b"22",
        b"token_file": str(session_file).encode("utf-8")[:220],
        b"session_id": session_id.encode("ascii"),
        b"protocol": b"1",
    }

    zeroconf = None
    try:
        zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
        info = ServiceInfo(
            SERVICE_TYPE,
            service_name,
            addresses=addresses,
            port=port,
            properties=properties,
            server=server_name,
        )
    except Exception:
        if zeroconf is not None:
            try:
                zeroconf.close()
            except Exception:
                pass
        session_file.unlink(missing_ok=True)
        raise
    try:
        zeroconf.register_service(info)
    except Exception:
        zeroconf.close()
        session_file.unlink(missing_ok=True)
        raise

    return LanWebAdvertisement(zeroconf, info, session_file, os.getpid(), device_id, session_id)
