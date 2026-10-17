"""Camera work returns through the real product event queue and WebSocket."""
import base64
import importlib
import json
from pathlib import Path
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.patchx import protocol
from test_patchx_protocol import mcp
from test_patchx_support import FakeAudio, FakeCodec, FakeTransport, wait_for
from test_patchx_camera import JPEG
from test_companion_transport import LoopbackServer

entry = importlib.import_module("apps.patchx.__main__")


class BlockingCapture:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.queries = []

    def capture(self, query, deadline, cancel_event, max_jpeg_bytes):
        from apps.patchx.camera import CameraFrame
        self.queries.append(query)
        self.started.set()
        if not self.release.wait(2):
            raise RuntimeError("fixture release timed out")
        return CameraFrame(JPEG, 640, 480, time.monotonic_ns(), {})


def live_config(url="ws://127.0.0.1:1"):
    return {"voice": {"mode": "live", "backend_url": url, "allow_insecure_ws": True,
                      "token_env": "", "device_id": "fixture-device", "client_id": "fixture-client"},
            "patchx": {"client_ip": "192.0.2.8"}}


class CameraIntegrationTests(unittest.TestCase):
    def test_jpeg_budget_includes_unicode_and_json_escape_overhead(self):
        for query in ("look", "看到了什么", "\x00" * 4096):
            budget = protocol.jpeg_budget(query)
            message = protocol.image_message(query, b"x" * budget)
            self.assertLessEqual(len(json.dumps(message, ensure_ascii=False).encode()), 65536)
            with self.assertRaises(ValueError):
                protocol.image_message(query, b"x" * (budget + 1))
        with self.assertRaises(ValueError):
            protocol.image_message("query", b"")

    def make_runtime(self, backend, url="ws://127.0.0.1:1", fake_transport=True):
        runtime = entry.create_runtime(live_config(url), audio_factory=FakeAudio,
            codec_factory=FakeCodec, transport_factory=FakeTransport if fake_transport else None,
            camera_backend=backend, http_address=("127.0.0.1", 0))
        self.addCleanup(runtime.close)
        self.addCleanup(backend.release.set)
        runtime.start()
        runtime.core.action("connect")
        wait_for(lambda: runtime.core.snapshot()["connected"])
        return runtime

    def test_capture_does_not_block_expression_and_no_old_photo_after_reconnect(self):
        backend = BlockingCapture()
        runtime = self.make_runtime(backend)
        core = runtime.core
        old_transport = core.transport
        core.post("message", mcp("vision_recognition", query="old"), core.generation)
        self.assertTrue(backend.started.wait(1))
        core.post("message", mcp("expression_control", expression_type=18), core.generation)
        wait_for(lambda: core.snapshot()["patchx"]["expression"]["id"] == 18)
        core.action("disconnect")
        core.action("connect")
        wait_for(lambda: core.snapshot()["connected"])
        backend.release.set()
        wait_for(lambda: not core.camera.snapshot()["busy"])
        core.action("unmute")  # Queue barrier after capture callback.
        self.assertFalse(any(isinstance(x, dict) and x.get("type") == "image"
                             for x in old_transport.sent + core.transport.sent))
        self.assertFalse(core.stopping.is_set())

    def test_two_original_camera_methods_send_real_wire_image_without_session_id(self):
        received = []
        done = threading.Event()
        release = threading.Event()
        def serve(connection, fixture):
            fixture.frame(connection)
            fixture.send(connection, 1, json.dumps({"type": "hello", "session_id": "camera-test",
                "audio_params": {"format": "opus", "sample_rate": 24000,
                                 "channels": 1, "frame_duration": 60}}).encode())
            for method in ("vision_recognition", "secondary_vision_capture"):
                fixture.send(connection, 1, json.dumps(mcp(method, query=method)).encode())
                opcode, payload = fixture.frame(connection)
                self.assertEqual(opcode, 1)
                received.append(json.loads(payload))
            done.set()
            release.wait(3)
        server = LoopbackServer(serve)
        backend = BlockingCapture()
        backend.release.set()
        runtime = self.make_runtime(backend, server.url, fake_transport=False)
        self.addCleanup(release.set)
        self.assertTrue(done.wait(3))
        for message in received:
            self.assertEqual(set(message), {"type", "text", "image_data"})
            self.assertEqual(message["type"], "image")
            self.assertEqual(base64.b64decode(message["image_data"]["data"], validate=True), JPEG)
            self.assertEqual(message["image_data"]["format"], "jpeg")
            self.assertEqual(message["image_data"]["url"], "")
        self.assertEqual([x["text"] for x in received], backend.queries)
        wait_for(lambda: runtime.core.snapshot()["patchx"]["camera"]["status"] == "sent")
        self.assertEqual(runtime.core.robot_status()["payload"]["camera"], "ok")
        release.set()
        self.assertTrue(server.done.wait(3))
        self.assertIsNone(server.failure)

    def test_wrong_session_and_prehello_never_start_capture(self):
        backend = BlockingCapture()
        runtime = self.make_runtime(backend)
        core = runtime.core
        core.post("message", dict(mcp("vision_recognition", query="wrong"), session_id="wrong"), core.generation)
        core.action("unmute")
        self.assertFalse(backend.started.is_set())
        core.action("disconnect")
        core.post("message", mcp("vision_recognition", query="offline"), core.generation)
        core.action("unmute")
        self.assertFalse(backend.started.is_set())


if __name__ == "__main__":
    unittest.main()
