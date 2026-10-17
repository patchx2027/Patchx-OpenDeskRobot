"""Local-device pairing: bounded sessions, no credentials in URLs or logs."""
import fcntl
import ipaddress
import os
from pathlib import Path
import secrets
import socket
import struct
import threading
import time
from http.cookies import SimpleCookie


def interface_addresses():
    addresses = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
        for _, name in socket.if_nameindex():
            try:
                raw = fcntl.ioctl(peer.fileno(), 0x8915, struct.pack("256s", name[:15].encode()))
                address = socket.inet_ntoa(raw[20:24])
                if ipaddress.ip_address(address).is_loopback:
                    continue
                wireless = (Path("/sys/class/net", name, "wireless").exists() or
                            Path("/sys/class/net", name, "phy80211").exists())
                addresses.append({"address": address, "kind": "wifi" if wireless else "wired"})
            except OSError:
                pass
    return addresses


def local_addresses():
    return {"127.0.0.1"} | {item["address"] for item in interface_addresses()}


def preferred_address(interfaces, host):
    wifi = [item["address"] for item in interfaces if item["kind"] == "wifi"]
    all_addresses = [item["address"] for item in interfaces]
    return (wifi[0] if wifi else host if host in all_addresses else
            all_addresses[0] if all_addresses else "127.0.0.1")


class Access:
    def __init__(self, enabled, code_file):
        self.enabled = enabled
        self.code = "".join(str(secrets.randbelow(10)) for _ in range(8))
        self.sessions = {}
        self.attempts = []
        self.lock = threading.Lock()
        if enabled:
            path = Path(code_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(self.code + "\n")

    @staticmethod
    def local(client):
        try:
            return ipaddress.ip_address(client).is_loopback
        except ValueError:
            return False

    def authorized(self, client, cookie):
        if self.local(client):
            return True
        if not self.enabled:
            return False
        try:
            parsed = SimpleCookie(cookie or "")
            token = parsed["rtctrl_session"].value
        except (KeyError, ValueError):
            return False
        with self.lock:
            return self.sessions.get(token, 0) > time.monotonic()

    def pair(self, code):
        if not isinstance(code, str) or len(code) != 8 or not code.isascii() or not code.isdigit():
            raise ValueError("配对码格式无效")
        with self.lock:
            now = time.monotonic()
            self.attempts = [t for t in self.attempts if now - t < 60]
            if len(self.attempts) >= 5:
                raise ValueError("尝试过于频繁，请一分钟后再试")
            self.attempts.append(now)
            if not secrets.compare_digest(code, self.code):
                raise ValueError("配对码不正确")
            self.sessions = {k: t for k, t in self.sessions.items() if t > now}
            if len(self.sessions) >= 16:
                self.sessions.pop(next(iter(self.sessions)))
            token = secrets.token_urlsafe(32)
            self.sessions[token] = now + 86400
            return token
