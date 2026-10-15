#!/usr/bin/env python3
"""Windows desktop companion for EmberOS running on a headless Pi.

Keep this app open before starting ``emberos --lan`` on the Pi. It discovers
the LAN service, opens an SSH tunnel, reads that session's token over SSH, and
opens the existing EmberOS GUI in a standalone Edge window.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import select
import secrets
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from urllib.parse import urlencode

import keyring
import paramiko
if sys.platform == "win32":
    from keyring.backends.Windows import WinVaultKeyring
from zeroconf import IPVersion, ServiceBrowser, ServiceListener, Zeroconf


SERVICE_TYPE = "_emberos._tcp.local."
KEYRING_SERVICE = "EmberOS Companion"
APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "EmberOS"
CONFIG_PATH = APP_DIR / "companion.json"


@dataclass(frozen=True)
class Device:
    device_id: str
    name: str
    service_name: str
    addresses: tuple[str, ...]
    web_port: int
    ssh_port: int
    token_file: str
    session_id: str


def _decode(value: bytes | None, default: str = "") -> str:
    if not value:
        return default
    return value.decode("utf-8", errors="replace")


def _fingerprint(key: paramiko.PKey) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    import base64

    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def _load_config() -> dict:
    try:
        value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(value, dict) and isinstance(value.get("devices", {}), dict):
            return value
    except (OSError, ValueError, TypeError):
        pass
    return {"devices": {}}


def _device_from_service_info(info, service_name: str) -> Device | None:
    properties = info.properties
    device_id = _decode(properties.get(b"device_id"))
    token_file = _decode(properties.get(b"token_file"))
    session_id = _decode(properties.get(b"session_id"))
    protocol = _decode(properties.get(b"protocol"))
    if not device_id or not token_file or not session_id or protocol != "1":
        return None
    addresses = tuple(info.parsed_addresses(IPVersion.V4Only))
    if not addresses:
        return None
    try:
        web_port = int(info.port)
        ssh_port = int(_decode(properties.get(b"ssh_port"), "22"))
    except (TypeError, ValueError):
        return None
    if not 1 <= web_port <= 65535 or not 1 <= ssh_port <= 65535:
        return None
    return Device(
        device_id=device_id,
        name=_decode(properties.get(b"device_name"), service_name),
        service_name=service_name,
        addresses=addresses,
        web_port=web_port,
        ssh_port=ssh_port,
        token_file=token_file,
        session_id=session_id,
    )


def _save_config(config: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2), encoding="utf-8")
    try:
        temporary.replace(CONFIG_PATH)
    finally:
        temporary.unlink(missing_ok=True)


class _ForwardHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server = self.server
        transport = server.transport
        if not transport or not transport.is_active():
            return
        try:
            channel = transport.open_channel(
                "direct-tcpip", ("127.0.0.1", server.remote_port), self.request.getpeername()
            )
        except (OSError, paramiko.SSHException):
            return
        if channel is None:
            return

        try:
            while True:
                readable, _, _ = select.select([self.request, channel], [], [], 20)
                if not readable:
                    if channel.closed:
                        break
                    continue
                for source in readable:
                    try:
                        data = source.recv(32768)
                    except OSError:
                        return
                    if not data:
                        return
                    destination = channel if source is self.request else self.request
                    destination.sendall(data)
        finally:
            channel.close()


class _ForwardServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, transport, remote_port: int):
        self.transport = transport
        self.remote_port = remote_port
        super().__init__(("127.0.0.1", 0), _ForwardHandler)


class _DeviceListener(ServiceListener):
    def __init__(self, app):
        self.app = app

    def add_service(self, zeroconf, service_type, name):
        self.app.resolve_service(zeroconf, service_type, name)

    def update_service(self, zeroconf, service_type, name):
        self.app.resolve_service(zeroconf, service_type, name)

    def remove_service(self, zeroconf, service_type, name):
        self.app.remove_service(name)


class _PinnedHostKey(paramiko.MissingHostKeyPolicy):
    def __init__(self, app, device: Device, expected: str | None):
        self.app = app
        self.device = device
        self.expected = expected
        self.observed = None

    def missing_host_key(self, client, hostname, key):
        actual = _fingerprint(key)
        if self.expected:
            if actual != self.expected:
                raise paramiko.SSHException(
                    "This Pi's SSH key changed. Disconnect and verify the device before pairing again."
                )
        elif not self.app.confirm_host_key(self.device, key.get_name(), actual):
            raise paramiko.SSHException("The Pi's SSH host key was not trusted.")
        client.get_host_keys().add(hostname, key.get_name(), key)
        self.observed = actual


class EmberOSCompanion:
    def __init__(self, background: bool = False):
        if sys.platform != "win32":
            raise RuntimeError("EmberOS Connect currently runs on Windows.")

        self.root = tk.Tk()
        self.root.title("EmberOS Connect")
        self.root.geometry("620x410")
        self.root.minsize(560, 340)
        self.config = _load_config()
        self.devices: dict[str, Device] = {}
        self.rows: dict[str, str] = {}
        self.connecting: set[str] = set()
        self.active: dict[str, tuple[paramiko.SSHClient, _ForwardServer, threading.Thread, str]] = {}
        self.zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
        self.listener = _DeviceListener(self)
        self.browser = ServiceBrowser(self.zeroconf, SERVICE_TYPE, listener=self.listener)

        self.status = tk.StringVar(value="Mencari Pi EmberOS di jaringan lokal…")
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        if background:
            self.root.after(200, self.root.iconify)

    def _build_ui(self):
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="EmberOS Connect", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text="Jalankan `emberos --lan` di Pi. Companion ini akan menemukannya dan membuka jendela app.",
            wraplength=570,
        ).pack(anchor="w", pady=(6, 14))

        columns = ("device", "address", "status")
        self.table = ttk.Treeview(frame, columns=columns, show="headings", height=9, selectmode="browse")
        self.table.heading("device", text="Perangkat")
        self.table.heading("address", text="Alamat jaringan")
        self.table.heading("status", text="Status")
        self.table.column("device", width=190)
        self.table.column("address", width=190)
        self.table.column("status", width=150)
        self.table.pack(fill="both", expand=True)
        self.table.bind("<Double-1>", lambda _event: self.connect_selected())

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(12, 4))
        self.connect_button = ttk.Button(buttons, text="Pair / Open EmberOS", command=self.connect_selected)
        self.connect_button.pack(side="left")
        ttk.Button(buttons, text="Disconnect", command=self.disconnect_selected).pack(side="left", padx=8)
        ttk.Button(buttons, text="Forget pairing", command=self.forget_selected).pack(side="left", padx=8)

        self.startup_var = tk.BooleanVar(value=self._startup_enabled())
        ttk.Checkbutton(
            frame,
            text="Jalankan companion saat Windows masuk",
            variable=self.startup_var,
            command=self._toggle_startup,
        ).pack(anchor="w", pady=(4, 0))
        ttk.Label(frame, textvariable=self.status, wraplength=570).pack(anchor="w", pady=(10, 0))

    def resolve_service(self, zeroconf, service_type, name):
        info = zeroconf.get_service_info(service_type, name, timeout=1800)
        if info is None:
            return
        device = _device_from_service_info(info, name)
        if device is None:
            return
        self.root.after(0, self.add_device, device)

    def remove_service(self, service_name):
        self.root.after(0, self._remove_service, service_name)

    def _remove_service(self, service_name):
        for device_id, device in list(self.devices.items()):
            if device.service_name == service_name:
                self._disconnect_device(device_id)
                del self.devices[device_id]
                row = self.rows.pop(device_id, None)
                if row and self.table.exists(row):
                    self.table.delete(row)
        if not self.devices:
            self.status.set("Menunggu EmberOS di Pi. Biarkan companion ini terbuka.")

    def add_device(self, device: Device):
        self.devices[device.device_id] = device
        paired = device.device_id in self.config.get("devices", {})
        status = "Paired" if paired else "Pair sekali"
        address = ", ".join(device.addresses[:2])
        row = self.rows.get(device.device_id)
        if row and self.table.exists(row):
            self.table.item(row, values=(device.name, address, status))
        else:
            row = self.table.insert("", "end", values=(device.name, address, status))
            self.rows[device.device_id] = row
        self.status.set(f"Pi ditemukan: {device.name} ({address}).")
        active = self.active.get(device.device_id)
        if active and active[3] != device.session_id:
            self._disconnect_device(device.device_id)
            active = None
        if paired and active is None and device.device_id not in self.connecting:
            self._connect_device(device, automatic=True)

    def _selected_device(self) -> Device | None:
        selected = self.table.selection()
        if not selected:
            return None
        for device_id, row in self.rows.items():
            if row == selected[0]:
                return self.devices.get(device_id)
        return None

    def connect_selected(self):
        device = self._selected_device()
        if device is None:
            messagebox.showinfo("Pilih Pi", "Tunggu sampai Pi muncul di daftar, lalu pilih perangkatnya.", parent=self.root)
            return
        if device.device_id in self.active:
            self.status.set(f"EmberOS sudah terbuka dari {device.name}.")
            return
        self._connect_device(device, automatic=False)

    def _connect_device(self, device: Device, automatic: bool):
        if device.device_id in self.connecting:
            return
        self.connecting.add(device.device_id)
        self.root.after(0, self.status.set, f"Menghubungkan ke {device.name} lewat SSH…")
        threading.Thread(
            target=self._connect_worker, args=(device, automatic), daemon=True
        ).start()

    def _ask_on_ui(self, function, timeout=180):
        done = threading.Event()
        result = {}

        def invoke():
            try:
                result["value"] = function()
            finally:
                done.set()

        self.root.after(0, invoke)
        if not done.wait(timeout):
            return None
        return result.get("value")

    def confirm_host_key(self, device: Device, key_type: str, fingerprint: str) -> bool:
        prompt = (
            f"Pi: {device.name}\nSSH key type: {key_type}\nSSH fingerprint: {fingerprint}\n\n"
            "Ini pairing pertama. Bandingkan fingerprint ini dengan hasil perintah berikut "
            "di Pi, lalu pilih Yes hanya jika sama:\n\n"
            "for key in /etc/ssh/ssh_host_*_key.pub; do sudo ssh-keygen -lf \"$key\"; done"
        )
        return bool(self._ask_on_ui(lambda: messagebox.askyesno("Verifikasi Pi", prompt, parent=self.root)))

    def _connect_worker(self, device: Device, automatic: bool):
        client = None
        forwarder = None
        try:
            saved = self.config.get("devices", {}).get(device.device_id, {})
            username = saved.get("username", "")
            if not username:
                username = self._ask_on_ui(
                    lambda: simpledialog.askstring(
                        "Pair dengan Pi",
                        f"Nama pengguna Linux untuk {device.name}:",
                        initialvalue="",
                        parent=self.root,
                    )
                )
                if not username:
                    raise RuntimeError("Pairing dibatalkan. Nama pengguna Pi diperlukan.")

            credential_id = f"{device.device_id}:{username}"
            password = None
            try:
                password = keyring.get_password(KEYRING_SERVICE, credential_id)
            except Exception:
                pass
            if password is None:
                password = self._ask_on_ui(
                    lambda: simpledialog.askstring(
                        "Pair dengan Pi",
                        f"Kata sandi SSH untuk {username}@{device.name}:\n"
                        "Disimpan di Windows Credential Manager agar pairing tidak perlu diulang. "
                        "Untuk SSH key, biarkan kosong.",
                        show="*",
                        parent=self.root,
                    )
                )
                if password is None:
                    raise RuntimeError("Pairing dibatalkan.")

            expected = saved.get("host_fingerprint")
            client = paramiko.SSHClient()
            client.load_system_host_keys()
            policy = _PinnedHostKey(self, device, expected)
            client.set_missing_host_key_policy(policy)

            # Use the discovered IP, then bind its SSH key to the stable mDNS
            # device id so changing DHCP leases does not trigger a new pairing.
            last_error = None
            for address in device.addresses:
                try:
                    client.connect(
                        hostname=address,
                        port=device.ssh_port,
                        username=username,
                        password=password or None,
                        look_for_keys=not bool(password),
                        allow_agent=not bool(password),
                        timeout=8,
                        banner_timeout=8,
                        auth_timeout=12,
                    )
                    break
                except Exception as exc:
                    last_error = exc
                    if client.get_transport() and client.get_transport().is_active():
                        client.close()
                        client = paramiko.SSHClient()
                        client.load_system_host_keys()
                        policy = _PinnedHostKey(self, device, expected)
                        client.set_missing_host_key_policy(policy)
            else:
                raise RuntimeError(f"SSH ke Pi gagal: {last_error}")

            remote_key = client.get_transport().get_remote_server_key()
            fingerprint = _fingerprint(remote_key)
            if expected and expected != fingerprint:
                raise RuntimeError("Kunci SSH Pi berubah; pairing dihentikan demi keamanan.")

            sftp = client.open_sftp()
            try:
                with sftp.open(device.token_file, "r") as session_file:
                    session = json.loads(session_file.read().decode("utf-8"))
            finally:
                sftp.close()
            token = session.get("token")
            if not isinstance(token, str) or not token:
                raise RuntimeError("Sesi EmberOS di Pi belum siap. Jalankan `emberos --lan` lagi.")
            remote_port = int(session.get("port", device.web_port))

            if password:
                try:
                    keyring.set_password(KEYRING_SERVICE, credential_id, password)
                except Exception as exc:
                    error_text = str(exc)
                    self.root.after(
                        0,
                        lambda error_text=error_text: messagebox.showwarning(
                            "Penyimpanan pairing",
                            "Koneksi berhasil, tetapi Windows Credential Manager tidak bisa "
                            f"menyimpan kata sandi: {error_text}",
                            parent=self.root,
                        ),
                    )

            self.config.setdefault("devices", {})[device.device_id] = {
                "name": device.name,
                "username": username,
                "host_fingerprint": fingerprint,
            }
            _save_config(self.config)

            forwarder = _ForwardServer(client.get_transport(), remote_port)
            forward_thread = threading.Thread(target=forwarder.serve_forever, daemon=True)
            forward_thread.start()
            local_port = forwarder.server_address[1]
            url = f"http://127.0.0.1:{local_port}/?{urlencode({'token': token})}"
            self._open_app_window(url)
            self.active[device.device_id] = (client, forwarder, forward_thread, device.session_id)
            client = None
            forwarder = None
            self.root.after(0, self.status.set, f"Terhubung ke {device.name}. Jendela EmberOS sudah dibuka.")
        except Exception as exc:
            message = str(exc)
            self.root.after(0, self.status.set, f"Gagal menghubungkan: {message}")
            if not automatic:
                self.root.after(
                    0,
                    lambda: messagebox.showerror("EmberOS Connect", message, parent=self.root),
                )
        finally:
            if forwarder is not None:
                forwarder.shutdown()
                forwarder.server_close()
            if client is not None:
                client.close()
            self.connecting.discard(device.device_id)

    def _open_app_window(self, url: str):
        candidates = []
        for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            root = os.environ.get(variable)
            if root:
                candidates.append(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
        edge = next((path for path in candidates if path.is_file()), None)
        if edge:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen([str(edge), f"--app={url}", "--no-first-run"], creationflags=flags)
        else:
            import webbrowser

            webbrowser.open_new(url)

    def forget_selected(self):
        device = self._selected_device()
        if not device:
            messagebox.showinfo("Pilih Pi", "Pilih perangkat yang pairing-nya ingin dihapus.", parent=self.root)
            return
        self._disconnect_device(device.device_id)
        saved = self.config.get("devices", {}).pop(device.device_id, None)
        if saved:
            credential_id = f"{device.device_id}:{saved.get('username', '')}"
            try:
                keyring.delete_password(KEYRING_SERVICE, credential_id)
            except Exception:
                pass
            _save_config(self.config)
        self.status.set(f"Pairing untuk {device.name} dihapus dari komputer ini.")

    def disconnect_selected(self):
        device = self._selected_device()
        if not device:
            messagebox.showinfo("Pilih Pi", "Pilih perangkat yang ingin diputus.", parent=self.root)
            return
        session = self.active.get(device.device_id)
        if session:
            self._disconnect_device(device.device_id)
            self.status.set(f"Koneksi ke {device.name} diputus.")
        else:
            self.status.set(f"Tidak ada koneksi aktif ke {device.name}.")

    def _disconnect_device(self, device_id: str):
        session = self.active.pop(device_id, None)
        if not session:
            return
        client, forwarder, _thread, _session_id = session
        try:
            forwarder.shutdown()
            forwarder.server_close()
        except Exception:
            pass
        client.close()

    def _startup_enabled(self) -> bool:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                winreg.QueryValueEx(key, "EmberOS Connect")
            return True
        except (OSError, ImportError):
            return False

    def _toggle_startup(self):
        try:
            import winreg

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                if self.startup_var.get():
                    if getattr(sys, "frozen", False):
                        command = f'"{sys.executable}" --background'
                    else:
                        command = f'"{sys.executable}" "{Path(__file__).resolve()}" --background'
                    winreg.SetValueEx(key, "EmberOS Connect", 0, winreg.REG_SZ, command)
                else:
                    try:
                        winreg.DeleteValue(key, "EmberOS Connect")
                    except FileNotFoundError:
                        pass
        except OSError as exc:
            self.startup_var.set(not self.startup_var.get())
            messagebox.showerror("Startup Windows", str(exc), parent=self.root)

    def close(self):
        for client, forwarder, _thread, _session_id in list(self.active.values()):
            try:
                forwarder.shutdown()
                forwarder.server_close()
            except Exception:
                pass
            client.close()
        self.active.clear()
        try:
            self.browser.cancel()
            self.zeroconf.close()
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--background", action="store_true", help="Start minimized for auto-discovery")
    parser.add_argument("--self-test", action="store_true", help="Check bundled Windows runtime dependencies")
    options = parser.parse_args()
    try:
        if options.self_test:
            if sys.platform != "win32":
                return 1
            if not isinstance(keyring.get_keyring(), WinVaultKeyring):
                return 1
            probe = f"self-test:{secrets.token_hex(12)}"
            keyring.set_password(KEYRING_SERVICE, probe, "EmberOS Connect credential test")
            try:
                if keyring.get_password(KEYRING_SERVICE, probe) != "EmberOS Connect credential test":
                    return 1
            finally:
                keyring.delete_password(KEYRING_SERVICE, probe)
            # Exercise the imports that the frozen executable needs at runtime.
            paramiko.SSHClient()
            _ForwardServer
            ServiceBrowser
            Zeroconf
            return 0
        EmberOSCompanion(background=options.background).run()
    except Exception as exc:
        if sys.platform == "win32":
            try:
                root = tk.Tk()
                root.withdraw()
                messagebox.showerror("EmberOS Connect", str(exc), parent=root)
                root.destroy()
            except Exception:
                pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
