"""Bounded cloud WebSocket transport; audio and reconnect policy belong to core."""
import json
import os
import threading
import time
from urllib.parse import urlsplit

try:
    import websocket
    from websocket._abnf import frame_buffer
except ImportError:
    websocket = None
    frame_buffer = object

TEXT_LIMIT = 64 * 1024
AUDIO_LIMIT = 4 * 1024


def _heartbeat_period(timeout):
    return max(5.0, timeout) * 3


class _BoundedFrameBuffer(frame_buffer):
    """Guard before payload allocation, including accumulated fragments.

    This private hook is regression-tested against pinned websocket-client 1.8.0.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.message_size = 0
        self.message_limit = TEXT_LIMIT

    def recv_length(self):
        super().recv_length()
        fin, _, _, _, opcode, _, _ = self.header
        if opcode in (1, 2):
            self.message_size = 0
            self.message_limit = TEXT_LIMIT if opcode == 1 else AUDIO_LIMIT
        if opcode in (0, 1, 2):
            self.message_size += self.length
            if self.message_size > self.message_limit:
                raise ValueError("Backend message exceeds size limit")
            if fin:
                self.message_size = 0
        elif self.length > 125:
            raise ValueError("Oversized WebSocket control frame")


class CloudTransport:
    def __init__(self, config, on_message, on_error):
        self.config = config
        self.on_message = on_message
        self.on_error = on_error
        self._socket = None
        self._receiver = None
        self._stop = threading.Event()
        self._lifecycle = threading.Lock()

    def connect(self):
        if websocket is None:
            raise RuntimeError("Cloud mode requires websocket-client==1.8.0; install requirements.txt")
        if getattr(websocket, "__version__", None) != "1.8.0":
            raise RuntimeError("Cloud mode requires websocket-client==1.8.0; install the pinned requirements")
        url = self.config.get("backend_url", "")
        parsed = urlsplit(url)
        if parsed.scheme not in ("ws", "wss") or not parsed.hostname:
            raise ValueError("backend_url must be a ws:// or wss:// URL")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("backend_url must not contain credentials or a fragment")
        if parsed.scheme == "ws" and not self.config.get("allow_insecure_ws", False):
            raise ValueError("Unencrypted ws requires explicit allow_insecure_ws=true")
        timeout = float(self.config.get("network_timeout_s", 5))
        if not 0 < timeout <= 60:
            raise ValueError("network_timeout_s must be within (0, 60]")
        heartbeat_enabled = self.config.get("websocket_heartbeat", True)
        if type(heartbeat_enabled) is not bool:
            raise ValueError("websocket_heartbeat must be boolean")
        headers = {
            "Protocol-Version": "1",
            "Device-Id": str(self.config.get("device_id", "rtctrl-companion")),
            "Client-Id": str(self.config.get("client_id", "rtctrl-companion")),
        }
        env_name = self.config.get("token_env", "")
        token = os.environ.get(env_name, "") if env_name else ""
        if env_name and not token:
            raise RuntimeError("Configured backend token environment variable is empty")
        if token:
            headers["Authorization"] = "Bearer " + token
        if any("\r" in value or "\n" in value for value in headers.values()):
            raise ValueError("Backend header values must not contain line breaks")
        with self._lifecycle:
            if self._socket is not None:
                raise RuntimeError("Backend already connected")
            ws = websocket.WebSocket(enable_multithread=True)
            ws.frame_buffer = _BoundedFrameBuffer(ws._recv, False)
            try:
                ws.settimeout(timeout)
                ws.connect(url, header=headers, timeout=timeout,
                           redirect_limit=0, suppress_origin=True)
                if ws.getstatus() != 101:
                    raise RuntimeError("Backend redirects are not allowed")
            except Exception:
                ws.shutdown()
                raise RuntimeError("Backend connection failed; check endpoint, TLS and credentials") from None
            self._stop = threading.Event()
            self._socket = ws
            heartbeat_period = _heartbeat_period(timeout) if heartbeat_enabled else None
            self._receiver = threading.Thread(
                target=self._receive, args=(ws, self._stop, heartbeat_period),
                name="companion-cloud", daemon=True)
            self._receiver.start()

    def _receive(self, ws, stop, heartbeat_period):
        last_received = time.monotonic()
        pending_ping = None
        pong_deadline = 0.0
        try:
            while not stop.is_set():
                now = time.monotonic()
                if heartbeat_period is not None:
                    if pending_ping is not None and now >= pong_deadline:
                        raise RuntimeError("Backend heartbeat timed out")
                    if pending_ping is None and now - last_received >= heartbeat_period:
                        pending_ping = os.urandom(8)
                        ws.ping(pending_ping)
                        pong_deadline = time.monotonic() + heartbeat_period
                try:
                    opcode, data = ws.recv_data(control_frame=True)
                except websocket.WebSocketTimeoutException:
                    continue
                if stop.is_set():
                    break
                last_received = time.monotonic()
                if opcode == websocket.ABNF.OPCODE_PONG:
                    if data == pending_ping:
                        pending_ping = None
                    continue
                if opcode == websocket.ABNF.OPCODE_PING:
                    # websocket-client already sends the matching pong.
                    continue
                if opcode == websocket.ABNF.OPCODE_CLOSE:
                    raise RuntimeError("Backend disconnected")
                if opcode == websocket.ABNF.OPCODE_BINARY:
                    if len(data) > AUDIO_LIMIT:
                        raise ValueError("Backend audio packet too large")
                    self.on_message(bytes(data))
                elif opcode == websocket.ABNF.OPCODE_TEXT:
                    if len(data) > TEXT_LIMIT:
                        raise ValueError("Backend text message too large")
                    message = json.loads(data)
                    if not isinstance(message, dict):
                        raise ValueError("Backend JSON must be an object")
                    self.on_message(message)
        except Exception:
            self._connection_error(ws, stop, "Backend disconnected or sent an invalid message")
        finally:
            stop.set()
            self._shutdown(ws)
            with self._lifecycle:
                if self._socket is ws:
                    self._socket = None

    @staticmethod
    def _shutdown(ws):
        try:
            ws.abort()
        except (OSError, AttributeError):
            pass
        try:
            ws.shutdown()
        except (OSError, AttributeError):
            pass

    def _connection_error(self, ws, stop, message):
        with self._lifecycle:
            notify = self._socket is ws and not stop.is_set()
            stop.set()
            if self._socket is ws:
                self._socket = None
        self._shutdown(ws)
        if notify:
            self.on_error(message)

    def send(self, message):
        with self._lifecycle:
            ws, stop = self._socket, self._stop
            if ws is None or stop.is_set():
                raise RuntimeError("Backend is not connected")
        if isinstance(message, dict):
            payload = json.dumps(message, ensure_ascii=False, allow_nan=False)
            if len(payload.encode("utf-8")) > TEXT_LIMIT:
                raise ValueError("Outgoing text message too large")
            opcode = websocket.ABNF.OPCODE_TEXT
        elif isinstance(message, bytes):
            payload = message
            if len(payload) > AUDIO_LIMIT:
                raise ValueError("Outgoing audio packet too large")
            opcode = websocket.ABNF.OPCODE_BINARY
        else:
            raise TypeError("Backend messages must be dict or bytes")
        try:
            ws.send(payload, opcode=opcode)
        except Exception:
            self._connection_error(ws, stop, "Backend send failed")
            raise RuntimeError("Backend send failed") from None

    def close(self):
        with self._lifecycle:
            self._stop.set()
            ws, receiver = self._socket, self._receiver
            self._socket = None
            self._receiver = None
        if ws is not None:
            self._shutdown(ws)
        if receiver is not None and receiver is not threading.current_thread():
            receiver.join(timeout=1)
