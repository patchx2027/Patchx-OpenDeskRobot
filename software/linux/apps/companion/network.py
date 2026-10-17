"""Bounded ConnMan Wi-Fi jobs. Passwords travel only through a no-echo PTY."""
import copy
import os
import pty
import re
import select
import shutil
import subprocess
import termios
import threading
import time

SERVICE = re.compile(r"wifi_[A-Za-z0-9_]+\Z")


def parse_services(output):
    networks = []
    seen = set()
    for line in output.splitlines():
        match = re.search(r"(wifi_[A-Za-z0-9_]+)\s*$", line)
        if not match:
            continue
        service = match.group(1)
        if service in seen:
            continue
        seen.add(service)
        # ConnMan reserves exactly three columns: Favorite, AutoConnect, State.
        flags = line[:3]
        prefix = line[3:match.start()].strip()
        hidden = not prefix or "_hidden_" in service
        security = "psk" if service.endswith("_managed_psk") else (
            "open" if service.endswith("_managed_none") else "unsupported")
        networks.append({"service": service, "ssid": prefix if not hidden else "隐藏网络",
                         "security": security, "connected": len(flags) == 3 and flags[2] in "RO",
                         "hidden": hidden})
    return networks[:128]


def parse_service_details(output):
    state = re.search(r"^\s*State\s*=\s*(\w+)", output, re.M)
    result = {"connected": bool(state and state.group(1) in ("ready", "online"))}
    address = re.search(r"^\s*IPv4\s*=.*?Address\s*=\s*([0-9.]+)", output, re.M)
    if address:
        parts = address.group(1).split(".")
        if len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts):
            result["ipv4"] = address.group(1)
    return result


class NetworkError(RuntimeError):
    pass


class NetworkManager:
    def __init__(self, enabled=False):
        self.enabled = bool(enabled)
        self._lock = threading.Lock()
        self._process_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._process = None
        self._pending_action = None
        self._next_refresh = 0.
        self._last_success = None
        self._data = {"available": self.enabled and bool(shutil.which("connmanctl")),
                      "busy": False, "status": "idle" if self.enabled else "disabled",
                      "error": "", "networks": [], "refreshing": False,
                      "stale": True, "last_updated": None, "refresh_error": "",
                      "service_available": None}
        if self.enabled and not self._data["available"]:
            self._data["error"] = "未找到 ConnMan 网络管理工具"

    def snapshot(self):
        with self._lock:
            if self._last_success is not None and time.monotonic() - self._last_success > 15:
                self._data["stale"] = True
            if (self._data["available"] and not self._stop.is_set()
                    and not self._data["busy"] and not self._data["refreshing"]
                    and time.monotonic() >= self._next_refresh):
                self._next_refresh = time.monotonic() + 5
                self._data["refreshing"] = True
                self._thread = threading.Thread(target=self._refresh_worker,
                                                name="companion-network", daemon=True)
                self._thread.start()
            return copy.deepcopy(self._data)

    def _refresh_worker(self):
        networks = None
        daemon_available = False
        try:
            # Read the daemon's current list; never enable, scan or connect here.
            deadline = time.monotonic() + 2
            ok, text = self._run(["services"], deadline)
            if not ok:
                raise NetworkError("无法读取无线网络状态")
            daemon_available = True
            networks = parse_services(text)
            # IPv4 is optional; cap detail queries even with multiple Wi-Fi radios.
            for network in [item for item in networks if item["connected"]][:2]:
                ok, details = self._run(["services", network["service"]], deadline)
                if not ok:
                    raise NetworkError("无法读取无线网络地址")
                network.update(parse_service_details(details))
                if not network["connected"]:
                    network.pop("ipv4", None)
        except Exception:
            networks = None
        with self._lock:
            self._data["refreshing"] = False
            if networks is None:
                self._data.update(stale=True, service_available=daemon_available,
                                  refresh_error="无线网络状态刷新失败，当前连接状态未知")
                for network in self._data["networks"]:
                    network["connected"] = False
                    network.pop("ipv4", None)
            else:
                self._last_success = time.monotonic()
                self._data.update(networks=networks, stale=False, last_updated=time.time(),
                                  refresh_error="", service_available=True)
            # One queued user action takes precedence over future refreshes.
            pending = self._pending_action
            self._pending_action = None
            if self._stop.is_set():
                pending = None
                self._data["busy"] = False
        if pending is not None:
            self._work(*pending)

    def action(self, body):
        if not isinstance(body, dict) or body.get("action") not in ("enable", "scan", "connect", "disconnect"):
            raise ValueError("不支持的无线网络操作")
        operation = body["action"]
        allowed = {"action"} if operation in ("enable", "scan") else ({"action", "service", "password"} if operation == "connect" else {"action", "service"})
        if set(body) - allowed:
            raise ValueError("无线网络请求包含不支持的字段")
        service = body.get("service", "")
        password = body.get("password", "")
        with self._lock:
            if not self._data["available"] or self._stop.is_set():
                raise ValueError("无线网络管理未启用或不可用")
            if self._data["busy"]:
                raise ValueError("正在处理无线网络操作，请稍后重试")
            security = None
            if operation not in ("enable", "scan"):
                if not isinstance(service, str) or not SERVICE.fullmatch(service):
                    raise ValueError("无效的无线网络，请重新扫描")
                network = next((item for item in self._data["networks"] if item["service"] == service), None)
                if network is None:
                    raise ValueError("无线网络不在扫描结果中，请重新扫描")
                security = network["security"]
                if operation == "connect":
                    if network.get("hidden") or security not in ("psk", "open"):
                        raise ValueError("暂不支持隐藏网络或企业认证网络")
                    if not isinstance(password, str) or len(password) > 64 or any(ord(c) < 32 or ord(c) == 127 for c in password):
                        raise ValueError("无线网络密码格式无效")
                    if security == "psk" and not (8 <= len(password.encode("utf-8")) <= 63 or re.fullmatch(r"[0-9a-fA-F]{64}", password)):
                        raise ValueError("无线网络密码需为8至63字节或64位十六进制密钥")
                    if security == "open" and password:
                        raise ValueError("开放网络不需要密码")
            self._data.update(busy=True, error="", status={"enable":"enabling", "scan":"scanning", "connect":"connecting", "disconnect":"disconnecting"}[operation])
            if self._data["refreshing"]:
                self._pending_action = (operation, service, password, security)
            else:
                self._thread = threading.Thread(target=self._work, args=(operation, service, password, security),
                                                name="companion-network", daemon=True)
                self._thread.start()
            return copy.deepcopy(self._data)

    def _spawn(self, argv, **kwargs):
        with self._process_lock:
            if self._stop.is_set():
                raise NetworkError("无线网络操作已取消")
            process = subprocess.Popen(argv, **kwargs)
            self._process = process
            return process

    def _dispose(self, process):
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=.5)
        with self._process_lock:
            if self._process is process:
                self._process = None

    def _run(self, args, deadline):
        if deadline <= time.monotonic() or self._stop.is_set():
            raise NetworkError("无线网络操作超时或已取消")
        process = self._spawn(["connmanctl"] + args, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        output = bytearray()
        try:
            while not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise NetworkError("无线网络操作超时")
                if not select.select([process.stdout], [], [], min(.1, remaining))[0]:
                    continue
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    process.wait(timeout=max(.01, deadline-time.monotonic()))
                    text = output.decode("utf-8", errors="replace")
                    failed = process.returncode != 0 or re.search(r"(?:^|\n)\s*(?:Error|Failed)\b", text, re.I)
                    return not failed, text
                if len(output) + len(chunk) > 65536:
                    raise NetworkError("无线网络工具输出异常，操作已停止")
                output.extend(chunk)
            raise NetworkError("无线网络操作已取消")
        finally:
            self._dispose(process)
            if process.stdout:
                process.stdout.close()

    def _scan(self, deadline):
        while True:
            ok, text = self._run(["scan", "wifi"], deadline)
            if ok:
                break
            if "not enabled" in text.lower() or "not powered" in text.lower():
                raise NetworkError("无线网卡尚未开启，请先点击开启无线网卡")
            if "no carrier" not in text.lower() or deadline - time.monotonic() <= 1:
                raise NetworkError("无线网络扫描失败，请检查网卡是否开启及 ConnMan 服务")
            if self._stop.wait(1):
                raise NetworkError("无线网络操作已取消")
        ok, text = self._run(["services"], deadline)
        if not ok:
            raise NetworkError("无法读取无线网络列表")
        with self._lock:
            self._data["networks"] = parse_services(text)

    def _connect(self, service, password, security, deadline):
        master, slave = pty.openpty()
        process = None
        try:
            attributes = termios.tcgetattr(slave)
            attributes[3] &= ~(termios.ECHO | termios.ECHONL)
            termios.tcsetattr(slave, termios.TCSANOW, attributes)
            process = self._spawn(["connmanctl"], stdin=slave, stdout=slave, stderr=slave,
                                  close_fds=True)
            os.close(slave)
            slave = None
            os.write(master, b"agent on\n")
            connected_command = False
            password_sent = False
            pending = ""
            while not self._stop.is_set() and time.monotonic() < deadline:
                if process.poll() is not None:
                    raise NetworkError("无线网络连接失败，请检查 ConnMan 服务")
                if not select.select([master], [], [], min(.1, max(0, deadline - time.monotonic())))[0]:
                    continue
                try:
                    data = os.read(master, 4096)
                except OSError:
                    raise NetworkError("无线网络连接失败，请检查 ConnMan 服务") from None
                if not data:
                    raise NetworkError("无线网络连接已中断")
                pending = (pending + data.decode("utf-8", errors="replace"))[-8192:]
                # readline may echo in userspace despite terminal ECHO being off.
                # Inspect only complete error lines, excluding credential echoes.
                lines = pending.splitlines(keepends=True)
                safe_lines = []
                for line in lines:
                    if password_sent and line.strip() in (password, "Passphrase? " + password,
                                                          "connmanctl> " + password):
                        continue
                    safe_lines.append(line)
                    if line.endswith(("\r", "\n")) and re.match(
                            r"^\s*(?:connmanctl>\s*)?(?:Error(?:\s|:)|Failed(?:\s|:)|Agent ReportError(?:\s|:))",
                            line, re.I):
                        raise NetworkError("无线网络连接失败，请检查密码、信号和认证方式")
                pending = "".join(safe_lines)
                if not connected_command and "agent registered" in pending.lower():
                    os.write(master, ("connect " + service + "\n").encode("ascii"))
                    connected_command = True
                    pending = ""
                elif connected_command and re.search(r"Passphrase\?\s*$", pending, re.I):
                    if password_sent or security != "psk":
                        raise NetworkError("无线网络认证失败，请检查密码")
                    os.write(master, password.encode("utf-8") + b"\n")
                    password_sent = True
                    pending = ""
                elif connected_command and re.search(r"Connected\s+" + re.escape(service) + r"(?:\s|$)", pending):
                    return
            raise NetworkError("无线网络连接超时或已取消")
        finally:
            self._dispose(process)
            os.close(master)
            if slave is not None:
                os.close(slave)

    def _work(self, operation, service, password, security):
        # Reserve one second for terminate/kill/reap within the 30/45 s job bound.
        deadline = time.monotonic() + (29 if operation in ("enable", "scan") else 44)
        error = ""
        try:
            if operation == "enable":
                ok, text = self._run(["enable", "wifi"], deadline)
                if not ok and "already enabled" not in text.lower():
                    raise NetworkError("无法启用无线网卡，请检查 ConnMan 服务和权限")
            elif operation == "scan":
                self._scan(deadline)
            else:
                if operation == "connect":
                    self._connect(service, password, security, deadline)
                else:
                    ok, _ = self._run(["disconnect", service], deadline)
                    if not ok:
                        raise NetworkError("无线网络断开失败")
                ok, text = self._run(["services", service], deadline)
                if not ok:
                    raise NetworkError("无法确认无线网络状态，请重新扫描")
                details = parse_service_details(text)
                if operation == "connect" and not details["connected"]:
                    raise NetworkError("无线网络尚未获得连接，请检查信号和路由器")
                with self._lock:
                    for item in self._data["networks"]:
                        if item["service"] == service:
                            item.pop("ipv4", None)
                            item.update(details)
                        elif operation == "connect":
                            item["connected"] = False
                            item.pop("ipv4", None)
        except NetworkError as exc:
            error = str(exc)
        except Exception:
            error = "无线网络操作失败，请检查 ConnMan 服务、权限和网卡"
        finally:
            # Never reflect command output or credentials into status/logs.
            password = None
            with self._lock:
                self._data.update(busy=False, status="error" if error else "idle", error=error)
                self._next_refresh = time.monotonic() + 5
                if not error and operation != "enable":
                    self._last_success = time.monotonic()
                    self._data.update(stale=False, last_updated=time.time(),
                                      refresh_error="", service_available=True)
                else:
                    self._data["stale"] = True
                    for network in self._data["networks"]:
                        network["connected"] = False
                        network.pop("ipv4", None)

    def close(self):
        self._stop.set()
        with self._lock:
            self._pending_action = None
        with self._process_lock:
            process = self._process
            if process is not None and process.poll() is None:
                process.terminate()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)
            if self._thread.is_alive():
                with self._process_lock:
                    if self._process is not None and self._process.poll() is None:
                        self._process.kill()
                self._thread.join(timeout=1)
