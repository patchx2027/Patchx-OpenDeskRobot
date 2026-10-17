"""Loopback-only HTTP control surface with bounded connections and same-origin writes."""
import http.client
import io
import json
import pathlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from .access import Access, local_addresses, interface_addresses, preferred_address

WEB = pathlib.Path(__file__).with_name("web")

class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 8

    def __init__(self, address, companion, network=None, device=None):
        self.companion = companion
        self.network = network
        self.device = device
        config = getattr(companion, "config", {})
        self.access = Access(config.get("lan_access", False), config.get("pairing_file", "/run/rtctrl-companion/pairing-code"))
        self.vision_url = config.get("vision_url", "http://127.0.0.1:8081")
        self.stream_slots = threading.BoundedSemaphore(2)
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        request.settimeout(15)
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

class Handler(BaseHTTPRequestHandler):
    server_version = "rtctrl-companion"
    def log_message(self, *args):
        pass

    def _reply(self, status, body, content_type="application/json; charset=utf-8", headers=None):
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.send_header("Connection", "close")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self):
        host = self.headers.get("Host", "")
        try:
            parsed = urlsplit("http://" + host)
            port = parsed.port
            return (parsed.hostname in (local_addresses() | {"localhost"} if self.server.access.enabled else {"localhost", "127.0.0.1"})
                    and port == self.server.server_port and not parsed.username and not parsed.password
                    and not parsed.path and not parsed.query and not parsed.fragment)
        except ValueError:
            return False

    def do_GET(self):
        if not self._host_ok():
            return self._reply(403, {"error": "invalid Host"})
        path = urlsplit(self.path).path
        access = self.server.access
        authorized = access.authorized(self.client_address[0], self.headers.get("Cookie"))
        if path in ("/api/access", "/api/access/qr.svg"):
            host = urlsplit("http://" + self.headers.get("Host", "")).hostname
            interfaces = interface_addresses()
            address = preferred_address(interfaces, host)
            url = "http://%s:%s/" % (address, self.server.server_port)
            if path.endswith("qr.svg"):
                try:
                    import qrcode
                    from qrcode.image.svg import SvgPathImage
                    target = io.BytesIO()
                    qrcode.make(url, image_factory=SvgPathImage, border=4).save(target)
                    return self._reply(200, target.getvalue(), "image/svg+xml")
                except ImportError:
                    return self._reply(503, {"error": "二维码组件未安装，请使用设备地址"})
            result = {"paired": authorized, "pairing_required": not authorized, "device_url": url,
                      "lan_enabled": access.enabled,
                      "device_addresses": [{"kind": item["kind"], "url": "http://%s:%s/" % (item["address"], self.server.server_port)} for item in interfaces]}
            if access.local(self.client_address[0]) and access.enabled:
                result["pairing_code"] = access.code
            return self._reply(200, result)
        if (path.startswith("/api/") or path.startswith("/vision/") or
                path in ("/stream.mjpg", "/snapshot.jpg", "/status.json")) and not authorized:
            return self._reply(401, {"error": "请先输入设备配对码"})
        vision = {"/vision/stream": "/stream.mjpg", "/vision/snapshot": "/snapshot.jpg",
                  "/stream.mjpg": "/stream.mjpg", "/snapshot.jpg": "/snapshot.jpg", "/status.json": "/status.json"}
        if path in vision:
            return self._vision(vision[path])
        if path in ("/api/logs", "/api/logs/export"):
            query = parse_qs(urlsplit(self.path).query)
            source, level = query.get("source", ["app"])[0], query.get("level", ["all"])[0]
            if source not in ("app", "system") or level not in ("all", "info", "warning", "error"):
                return self._reply(400, {"error": "日志筛选参数无效"})
            diagnostics = getattr(self.server.companion, "diagnostics", None)
            result = diagnostics.snapshot(source=source, level=level) if diagnostics else {"entries": [], "available": False}
            if path.endswith("/export"):
                payload = json.dumps(result, ensure_ascii=False, indent=2).encode()
                return self._reply(200, payload, "application/json; charset=utf-8",
                                   {"Content-Disposition": 'attachment; filename="device-diagnostics.json"'})
            return self._reply(200, result)
        if path == "/api/device":
            return self._reply(200, self.server.device.snapshot() if self.server.device else {"available": False, "busy": False, "error": "", "supported": {}})
        if path == "/api/network":
            return self._reply(200, self.server.network.snapshot() if self.server.network else {"available": False, "busy": False, "status": "未启用Wi-Fi管理", "error": "", "networks": []})
        if path == "/api/status":
            return self._reply(200, self.server.companion.snapshot())
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8")}
        if path not in assets:
            return self._reply(404, {"error": "not found"})
        name, mime = assets[path]
        self._reply(200, (WEB / name).read_bytes(), mime)

    def do_POST(self):
        origin = self.headers.get("Origin")
        expected = "http://" + self.headers.get("Host", "")
        if not self._host_ok() or (origin is not None and origin != expected):
            return self._reply(403, {"error": "cross-origin request rejected"})
        if self.path not in ("/api/action", "/api/network", "/api/device", "/api/pair"):
            return self._reply(404, {"error": "not found"})
        if self.path != "/api/pair" and not self.server.access.authorized(self.client_address[0], self.headers.get("Cookie")):
            return self._reply(401, {"error": "请先输入设备配对码"})
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self._reply(415, {"error": "application/json required"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024 or self.headers.get("Transfer-Encoding"):
                raise ValueError("invalid body size")
            body = json.loads(self.rfile.read(length))
            if self.path == "/api/pair":
                if not self.server.access.enabled or not isinstance(body, dict) or set(body) != {"code"}:
                    raise ValueError("配对请求无效")
                token = self.server.access.pair(body["code"])
                return self._reply(200, {"paired": True}, headers={"Set-Cookie":
                    "rtctrl_session=" + token + "; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400"})
            if self.path == "/api/device":
                if not self.server.device:
                    return self._reply(400, {"error": "未启用设备设置"})
                return self._reply(200, self.server.device.action(body))
            if self.path == "/api/network":
                if not self.server.network:
                    return self._reply(400, {"error": "未启用Wi-Fi管理"})
                try:
                    result = self.server.network.action(body)
                except ValueError as exc:
                    return self._reply(400, {"error": str(exc)})
                return self._reply(202, result)
            if not isinstance(body, dict) or set(body) != {"action"} or not isinstance(body["action"], str):
                raise ValueError("expected an action")
            result = self.server.companion.action(body["action"])
            self._reply(200, result)
        except (json.JSONDecodeError, UnicodeError):
            self._reply(400, {"error": "请求格式无效"})
        except ValueError as exc:
            self._reply(400, {"error": str(exc)})
        except TimeoutError:
            self._reply(408, {"error": "request timed out"})

    def _vision(self, path):
        streaming = path == "/stream.mjpg"
        if streaming and not self.server.stream_slots.acquire(blocking=False):
            return self._reply(503, {"error": "预览连接已满，请关闭其他预览"})
        origin = urlsplit(self.server.vision_url)
        connection = http.client.HTTPConnection(origin.hostname, origin.port, timeout=3)
        headers_sent = False
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            if response.status != 200:
                return self._reply(503, {"error": "摄像头服务暂不可用"})
            if not streaming:
                data = response.read(2097153)
                if len(data) > 2097152:
                    raise ValueError("oversized preview")
                return self._reply(200, data, "application/json" if path.endswith("json") else "image/jpeg")
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            headers_sent = True
            while True:
                block = response.read1(16384)
                if not block:
                    break
                self.wfile.write(block)
        except (OSError, ValueError, http.client.HTTPException):
            if not headers_sent:
                self._reply(503, {"error": "摄像头服务暂不可用"})
        finally:
            connection.close()
            if streaming:
                self.server.stream_slots.release()
