"""Cloud adapter tests: no backend, microphone or board is contacted."""
import json
import os
import pathlib
import sys
import queue
import socket
import struct
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from apps.companion import transport as t
from apps.companion.config import validate


class FakeSocket:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.messages = queue.Queue()
        self.sent = []
        self.stopped = False
        self.status = 101
    def _recv(self, size):
        raise AssertionError("No real socket reads")
    def settimeout(self, timeout):
        self.timeout = timeout
    def connect(self, url, **kwargs):
        self.connect_options = kwargs
    def getstatus(self):
        return self.status
    def recv_data(self, control_frame=False):
        item = self.messages.get(timeout=2)
        if isinstance(item, Exception):
            raise item
        return item
    def ping(self, data):
        self.sent.append((9, data))
    def send(self, data, opcode):
        self.sent.append((opcode, data))
    def shutdown(self):
        if not self.stopped:
            self.stopped = True
            self.messages.put((8, b""))
    def abort(self):
        self.shutdown()


@unittest.skipIf(t.websocket is None, "Install companion requirements for real-library tests")
class TransportTest(unittest.TestCase):
    def setUp(self):
        self.ws = FakeSocket()
        self.messages = []
        self.errors = []
        self.arrival = threading.Event()
        def message(value):
            self.messages.append(value)
            self.arrival.set()
        def error(value):
            self.errors.append(value)
            self.arrival.set()
        self.config = {"backend_url": "wss://localhost/service", "network_timeout_s": 1}
        self.client = t.CloudTransport(self.config, message, error)
        self.factory = patch.object(t.websocket, "WebSocket", return_value=self.ws)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.addCleanup(self.client.close)

    def test_incompatible_dependency_version_rejected(self):
        with patch.object(t.websocket, "__version__", "99.0"), self.assertRaisesRegex(RuntimeError, "1.8.0"):
            self.client.connect()

    def test_headers_tls_defaults_and_send(self):
        self.config["token_env"] = "COMPANION_TEST_TOKEN"
        with patch.dict(os.environ, {"COMPANION_TEST_TOKEN": "private"}):
            self.client.connect()
        self.assertEqual(self.ws.connect_options["header"]["Authorization"], "Bearer private")
        self.assertEqual(self.ws.connect_options["redirect_limit"], 0)
        self.assertNotIn("sslopt", self.ws.connect_options)
        self.client.send({"type": "hello"})
        self.client.send(b"opus")
        self.assertEqual(json.loads(self.ws.sent[0][1]), {"type": "hello"})
        self.assertEqual(self.ws.sent[1], (2, b"opus"))

    def test_reject_insecure_and_missing_token(self):
        self.config["backend_url"] = "ws://localhost"
        with self.assertRaises(ValueError):
            self.client.connect()
        self.config["allow_insecure_ws"] = True
        self.config["token_env"] = "COMPANION_MISSING_TOKEN"
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(RuntimeError):
            self.client.connect()

    def test_header_injection_rejected(self):
        self.config["device_id"] = "device\r\nInjected: yes"
        with self.assertRaises(ValueError):
            self.client.connect()

    def test_direct_transport_rejects_non_boolean_heartbeat_before_connect(self):
        for value in (0, 1, "false", None, [], {}):
            with self.subTest(value=value):
                self.config["websocket_heartbeat"] = value
                with self.assertRaisesRegex(ValueError, "websocket_heartbeat"):
                    self.client.connect()
                self.assertIsNone(self.client._socket)

    def test_redirect_rejected(self):
        self.ws.status = 302
        with self.assertRaises(RuntimeError):
            self.client.connect()
        self.assertTrue(self.ws.stopped)

    def test_receive_object_and_binary(self):
        self.client.connect()
        self.ws.messages.put((1, b'{"type":"hello"}'))
        self.assertTrue(self.arrival.wait(1))
        self.arrival.clear()
        self.ws.messages.put((2, b"audio"))
        self.assertTrue(self.arrival.wait(1))
        self.assertEqual(self.messages, [{"type": "hello"}, b"audio"])

    def test_malformed_message_reports_safe_failure(self):
        self.client.connect()
        self.ws.messages.put((1, b"private server text"))
        self.assertTrue(self.arrival.wait(1))
        self.assertEqual(len(self.errors), 1)
        self.assertNotIn("private", self.errors[0])

    def test_disconnect_and_limits(self):
        self.client.connect()
        with self.assertRaises(ValueError):
            self.client.send(b"x" * (t.AUDIO_LIMIT + 1))
        with self.assertRaises(ValueError):
            self.client.send({"value": "x" * t.TEXT_LIMIT})
        self.ws.messages.put((8, b""))
        self.assertTrue(self.arrival.wait(1))
        self.assertEqual(len(self.errors), 1)

    def test_close_inside_callback(self):
        finished = threading.Event()
        def callback(message):
            self.client.close()
            finished.set()
        self.client.on_message = callback
        self.client.connect()
        self.ws.messages.put((1, b"{}"))
        self.assertTrue(finished.wait(1))
        self.assertEqual(self.errors, [])

    def test_explicit_close_has_no_error(self):
        self.client.connect()
        self.client.close()
        self.assertTrue(self.ws.stopped)
        self.assertEqual(self.errors, [])


@unittest.skipIf(t.websocket is None, "Install companion requirements for frame-limit tests")
class FrameLimitTest(unittest.TestCase):
    def buffer(self, data):
        remaining = bytearray(data)
        def recv(size):
            if not remaining:
                raise AssertionError("Payload should not be read")
            result = bytes(remaining[:size])
            del remaining[:size]
            return result
        return t._BoundedFrameBuffer(recv, False)

    def test_large_frame_rejected_before_payload_read(self):
        buffer = self.buffer(bytes([0x82, 126]) + struct.pack("!H", t.AUDIO_LIMIT + 1))
        with self.assertRaises(ValueError):
            buffer.recv_frame()

    def test_fragmented_message_cumulative_limit(self):
        data = bytes([0x02, 126]) + struct.pack("!H", 3000) + b"x" * 3000
        data += bytes([0x80, 126]) + struct.pack("!H", 2000)
        buffer = self.buffer(data)
        self.assertEqual(len(buffer.recv_frame().data), 3000)
        with self.assertRaises(ValueError):
            buffer.recv_frame()

    def test_ping_between_fragments_keeps_budget(self):
        data = bytes([0x02, 126]) + struct.pack("!H", 3000) + b"x" * 3000
        data += bytes([0x89, 0])
        data += bytes([0x80, 126]) + struct.pack("!H", 2000)
        buffer = self.buffer(data)
        buffer.recv_frame()
        buffer.recv_frame()
        with self.assertRaises(ValueError):
            buffer.recv_frame()


class MissingDependencyTest(unittest.TestCase):
    def test_optional_dependency_error(self):
        client = t.CloudTransport({}, lambda value: None, lambda value: None)
        with patch.object(t, "websocket", None), self.assertRaisesRegex(RuntimeError, "requirements"):
            client.connect()


class HeartbeatConfigurationTest(unittest.TestCase):
    def test_companion_keeps_heartbeat_enabled_by_default(self):
        self.assertIs(validate({}).get("websocket_heartbeat"), True)

    def test_heartbeat_accepts_explicit_boolean_override(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                try:
                    config = validate({"websocket_heartbeat": enabled})
                except ValueError as error:
                    self.fail("Valid heartbeat boolean was rejected: " + str(error))
                self.assertIs(config["websocket_heartbeat"], enabled)

    def test_heartbeat_rejects_non_boolean_values(self):
        for value in (0, 1, "false", None, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate({"websocket_heartbeat": value})


class LoopbackServer:
    """One RFC6455 upgrade, bound only to loopback; no external dependencies."""
    def __init__(self, handler):
        import socket
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(3)
        self.url = "ws://127.0.0.1:%d/service" % self.listener.getsockname()[1]
        self.handler = handler
        self.failure = None
        self.done = threading.Event()
        self.request = ""
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    @staticmethod
    def read_exact(connection, size):
        data = b""
        while len(data) < size:
            chunk = connection.recv(size - len(data))
            if not chunk:
                raise EOFError("client closed")
            data += chunk
        return data

    @classmethod
    def frame(cls, connection):
        header = cls.read_exact(connection, 2)
        size = header[1] & 127
        if size == 126:
            size = struct.unpack("!H", cls.read_exact(connection, 2))[0]
        elif size == 127:
            size = struct.unpack("!Q", cls.read_exact(connection, 8))[0]
        if size > t.TEXT_LIMIT:
            raise AssertionError("Unexpected oversized client frame")
        if not header[1] & 128:
            raise AssertionError("Client frame must be masked")
        mask = cls.read_exact(connection, 4)
        data = cls.read_exact(connection, size)
        return header[0] & 15, bytes(value ^ mask[i % 4] for i, value in enumerate(data))

    @staticmethod
    def send(connection, opcode, data):
        length = len(data)
        if length < 126:
            header = bytes([128 | opcode, length])
        else:
            header = bytes([128 | opcode, 126]) + struct.pack("!H", length)
        connection.sendall(header + data)

    def run(self):
        import base64
        import hashlib
        try:
            with self.listener.accept()[0] as connection:
                connection.settimeout(3)
                data = b""
                while not data.endswith(b"\r\n\r\n"):
                    data += self.read_exact(connection, 1)
                    if len(data) > 8192:
                        raise AssertionError("Oversized handshake")
                self.request = data.decode("ascii")
                headers = dict(line.split(": ", 1) for line in self.request.split("\r\n")[1:] if ": " in line)
                key = headers["Sec-WebSocket-Key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
                accept = base64.b64encode(hashlib.sha1(key.encode("ascii")).digest())
                connection.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")
                self.handler(connection, self)
        except Exception as error:
            self.failure = error
        finally:
            self.listener.close()
            self.done.set()

    def finish(self, test):
        test.assertTrue(self.done.wait(4), "Loopback server did not finish")
        self.thread.join(timeout=1)
        if self.failure:
            raise self.failure


@unittest.skipIf(t.websocket is None, "Install companion requirements for loopback integration")
class LoopbackTransportTest(unittest.TestCase):
    def test_real_upgrade_audio_ping_and_peer_close(self):
        received = []
        messages = []
        errors = []
        failure = threading.Event()
        def handler(connection, server):
            received.append(server.frame(connection))
            received.append(server.frame(connection))
            server.send(connection, 1, json.dumps({
                "type": "hello", "session_id": "loopback",
                "audio_params": {"format": "opus", "sample_rate": 24000,
                                 "channels": 1, "frame_duration": 60}}).encode())
            server.send(connection, 9, b"health")
            received.append(server.frame(connection))
            server.send(connection, 2, b"opus-response")
            server.send(connection, 8, struct.pack("!H", 1000))
            received.append(server.frame(connection))
        server = LoopbackServer(handler)
        def error(value):
            errors.append(value)
            failure.set()
        client = t.CloudTransport({"backend_url": server.url, "allow_insecure_ws": True,
                                   "network_timeout_s": 1}, messages.append, error)
        self.addCleanup(client.close)
        with patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
            client.connect()
            client.send({"type": "hello", "audio_params": {"sample_rate": 16000}})
            client.send(b"opus-request")
            self.assertTrue(failure.wait(4))
        server.finish(self)
        self.assertEqual(received[0][0], 1)
        self.assertEqual(json.loads(received[0][1])["type"], "hello")
        self.assertEqual(received[1], (2, b"opus-request"))
        self.assertEqual(received[2], (10, b"health"))
        self.assertEqual(received[3][0], 8)
        self.assertEqual(messages[0]["audio_params"]["sample_rate"], 24000)
        self.assertEqual(messages[1], b"opus-response")
        self.assertEqual(len(errors), 1)
        self.assertIn("Protocol-Version: 1", server.request)

    def test_real_oversized_header_rejected_without_payload(self):
        errors = []
        failed = threading.Event()
        def handler(connection, server):
            # The server deliberately provides no payload: receiver must reject
            # the declared size immediately instead of waiting or allocating it.
            connection.sendall(bytes([0x82, 127]) + struct.pack("!Q", 2 ** 40))
            if connection.recv(1) != b"":
                raise AssertionError("Expected client socket closure")
        server = LoopbackServer(handler)
        def error(value):
            errors.append(value)
            failed.set()
        client = t.CloudTransport({"backend_url": server.url, "allow_insecure_ws": True,
                                   "network_timeout_s": 1}, lambda value: self.fail("Unexpected message"), error)
        self.addCleanup(client.close)
        with patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
            client.connect()
            self.assertTrue(failed.wait(2), "Did not reject oversized header immediately")
        server.finish(self)
        self.assertEqual(len(errors), 1)


@unittest.skipIf(t.websocket is None, "Install companion requirements for heartbeat integration")
class HeartbeatTransportTest(unittest.TestCase):
    def test_disabled_heartbeat_waits_for_speech_reply_without_pong(self):
        received = []
        messages = []
        errors = []
        finished = threading.Event()
        def handler(connection, server):
            # Read timeouts must continue across both the ping and pong windows.
            # Ignore any unsolicited ping so the old behavior really times out.
            connection.settimeout(.02)
            deadline = time.monotonic() + .2
            while time.monotonic() < deadline:
                try:
                    received.append(server.frame(connection))
                except socket.timeout:
                    continue
                except EOFError:
                    return
            connection.settimeout(3)
            server.send(connection, 9, b"server-health")
            received.append(server.frame(connection))
            server.send(connection, 1, b'{"type":"stt","text":"loopback speech"}')
            server.send(connection, 2, b"opus-response")
            if connection.recv(1) != b"":
                raise AssertionError("Expected client close")
        server = LoopbackServer(handler)
        def on_message(value):
            messages.append(value)
            if isinstance(value, bytes):
                finished.set()
        def on_error(value):
            errors.append(value)
            finished.set()
        client = t.CloudTransport({"backend_url": server.url, "allow_insecure_ws": True,
                                   "network_timeout_s": .02, "websocket_heartbeat": False},
                                  on_message, on_error)
        self.addCleanup(client.close)
        with patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}), patch.object(t, "_heartbeat_period", return_value=.05):
            client.connect()
            self.assertTrue(finished.wait(2), "Speech reply did not arrive")
            client.close()
        server.finish(self)
        self.assertEqual(messages, [{"type": "stt", "text": "loopback speech"}, b"opus-response"])
        self.assertEqual(errors, [])
        self.assertEqual(received, [(10, b"server-health")])

    def test_disabled_heartbeat_still_rejects_close_and_invalid_messages(self):
        for case in ("close", "oversized", "not-object"):
            with self.subTest(case=case):
                errors = []
                failed = threading.Event()
                def handler(connection, server):
                    if case == "close":
                        server.send(connection, 8, struct.pack("!H", 1000))
                        if server.frame(connection)[0] != 8:
                            raise AssertionError("Expected close response")
                    elif case == "oversized":
                        connection.sendall(bytes([0x82, 127]) + struct.pack("!Q", 2 ** 40))
                    else:
                        server.send(connection, 1, b"[]")
                    if connection.recv(1) != b"":
                        raise AssertionError("Expected client socket closure")
                server = LoopbackServer(handler)
                def on_error(value):
                    errors.append(value)
                    failed.set()
                client = t.CloudTransport({"backend_url": server.url, "allow_insecure_ws": True,
                                           "network_timeout_s": .02, "websocket_heartbeat": False},
                                          lambda value: self.fail("Invalid message was delivered"), on_error)
                self.addCleanup(client.close)
                with patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
                    client.connect()
                    self.assertTrue(failed.wait(2), "Invalid peer did not disconnect")
                    client.close()
                server.finish(self)
                self.assertEqual(len(errors), 1)
                self.assertIsNone(client._socket)

    def test_idle_ping_matching_pong_keeps_connection_alive(self):
        received = []
        messages = []
        errors = []
        ready = threading.Event()
        def handler(connection, server):
            for _ in range(2):
                opcode, payload = server.frame(connection)
                received.append(opcode)
                if opcode != 9 or not payload:
                    raise AssertionError("Expected client heartbeat ping")
                server.send(connection, 10, payload)
            server.send(connection, 1, b'{"type":"heartbeat-ok"}')
            if connection.recv(1) != b"":
                raise AssertionError("Expected client close")
        server = LoopbackServer(handler)
        def on_message(value):
            messages.append(value)
            ready.set()
        client = t.CloudTransport({"backend_url": server.url, "allow_insecure_ws": True,
                                   "network_timeout_s": .02, "websocket_heartbeat": True}, on_message, errors.append)
        self.addCleanup(client.close)
        with patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}), patch.object(t, "_heartbeat_period", return_value=.05):
            client.connect()
            self.assertTrue(ready.wait(2), "Two heartbeats failed")
            client.close()
        server.finish(self)
        self.assertEqual(received, [9, 9])
        self.assertEqual(messages, [{"type": "heartbeat-ok"}])
        self.assertEqual(errors, [])

    def test_missing_pong_closes_blackhole_connection(self):
        self.check_missing_pong(False)

    def test_wrong_pong_does_not_satisfy_heartbeat(self):
        self.check_missing_pong(True)

    def check_missing_pong(self, wrong_pong):
        errors = []
        failed = threading.Event()
        def handler(connection, server):
            opcode, payload = server.frame(connection)
            if opcode != 9:
                raise AssertionError("Expected client heartbeat ping")
            if wrong_pong:
                server.send(connection, 10, b"wrong-" + payload)
            if connection.recv(1) != b"":
                raise AssertionError("Expected heartbeat timeout closure")
        server = LoopbackServer(handler)
        def on_error(value):
            errors.append(value)
            failed.set()
        client = t.CloudTransport({"backend_url": server.url, "allow_insecure_ws": True,
                                   "network_timeout_s": .02}, lambda value: None, on_error)
        self.addCleanup(client.close)
        with patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}), patch.object(t, "_heartbeat_period", return_value=.05):
            client.connect()
            self.assertTrue(failed.wait(2), "Missing pong did not disconnect")
        server.finish(self)
        self.assertEqual(len(errors), 1)
        self.assertIsNone(client._socket)

    def test_stale_send_failure_cannot_close_new_connection(self):
        client = t.CloudTransport({}, lambda value: None, lambda value: self.fail("Stale error notified"))
        old, current = FakeSocket(), FakeSocket()
        old_stop = threading.Event()
        old_stop.set()
        client._socket = current
        client._connection_error(old, old_stop, "stale send error")
        self.assertIs(client._socket, current)
        self.assertFalse(current.stopped)
        client.close()


if __name__ == "__main__":
    unittest.main()
