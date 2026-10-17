"""Actual WebSocket upgrade and HTTP product status on loopback only."""
import importlib
import json
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.request
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from apps.patchx.config import validate_product
from apps.companion.transport import websocket
from test_companion_transport import LoopbackServer
from test_patchx_protocol import mcp
from test_patchx_support import FakeAudio, FakeCodec, wait_for
try:
    entry = importlib.import_module("apps.patchx.__main__")
except ModuleNotFoundError:
    entry = None

ROOT = pathlib.Path(__file__).resolve().parents[1]


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(entry, "PatchX executable entry has not been migrated")

    @unittest.skipIf(websocket is None, "Pinned websocket-client is required for real loopback test")
    def test_real_websocket_headers_hello_mcp_and_http_snapshot(self):
        received = []
        hello_received = threading.Event()
        release = threading.Event()
        def backend(connection, fixture):
            opcode, payload = LoopbackServer.frame(connection)
            received.append(json.loads(payload))
            hello_received.set()
            for message in ({"type": "hello", "session_id": "loopback", "audio_params": {
                                "format": "opus", "sample_rate": 24000, "channels": 1, "frame_duration": 60}},
                            mcp("expression_control", expression_type=18),
                            mcp("set_brightness", brightness=30)):
                LoopbackServer.send(connection, 1, json.dumps(message).encode())
            release.wait(3)
        cloud = LoopbackServer(backend)
        config = validate_product({"voice": {"mode": "live", "backend_url": cloud.url,
                     "allow_insecure_ws": True, "token_env": "", "device_id": "fixture-device",
                     "client_id": "fixture-client", "network_timeout_s": 1},
                     "patchx": {"client_ip": "192.0.2.8"}})
        runtime = entry.create_runtime(config, audio_factory=FakeAudio, codec_factory=FakeCodec,
                                       http_address=("127.0.0.1", 0))
        self.addCleanup(runtime.close)
        self.addCleanup(release.set)
        runtime.start()
        runtime.core.action("connect")
        self.assertTrue(hello_received.wait(2))
        wait_for(lambda: runtime.core.snapshot()["patchx"]["processed_messages"] == 2)
        url = "http://127.0.0.1:%d/api/status" % runtime.server.server_port
        with urllib.request.urlopen(url, timeout=2) as response:
            status = json.load(response)
        self.assertEqual(received[0]["client_ip"], "192.0.2.8")
        self.assertTrue(received[0]["trace_id"])
        self.assertEqual(received[0]["device_id"], "fixture-device")
        self.assertIn("Device-Id: fixture-device", cloud.request)
        self.assertIn("Client-Id: fixture-client", cloud.request)
        self.assertEqual(status["patchx"]["expression"]["id"], 18)
        self.assertFalse(status["patchx"]["expression"]["rendered"])
        self.assertEqual(status["patchx"]["last_result"]["status"], "unavailable")
        self.assertTrue(status["connected"])
        self.assertTrue(status["muted"])
        self.assertEqual(runtime.core.audio.rate, 24000)
        release.set()
        self.assertTrue(cloud.done.wait(3))
        self.assertIsNone(cloud.failure)

    @unittest.skipIf(websocket is None, "Pinned websocket-client is required for real loopback test")
    def test_real_websocket_prehello_and_wrong_session_do_not_dispatch(self):
        release = threading.Event()
        def backend(connection, fixture):
            fixture.frame(connection)
            for message in (mcp("expression_control", expression_type=3),
                            {"type": "hello", "session_id": "accepted", "audio_params": {
                                "format": "opus", "sample_rate": 24000, "channels": 1, "frame_duration": 60}},
                            dict(mcp("expression_control", expression_type=9), session_id="rejected"),
                            dict(mcp("expression_control", expression_type=18), session_id="accepted")):
                fixture.send(connection, 1, json.dumps(message).encode())
            release.wait(3)
        cloud = LoopbackServer(backend)
        config = validate_product({"voice": {"mode": "live", "backend_url": cloud.url,
                     "allow_insecure_ws": True, "token_env": "", "device_id": "fixture-device",
                     "client_id": "fixture-client", "network_timeout_s": 1},
                     "patchx": {"client_ip": "192.0.2.8"}})
        runtime = entry.create_runtime(config, audio_factory=FakeAudio, codec_factory=FakeCodec,
                                       http_address=("127.0.0.1", 0))
        self.addCleanup(runtime.close)
        self.addCleanup(release.set)
        runtime.start()
        runtime.core.action("connect")
        wait_for(lambda: runtime.core.snapshot()["patchx"]["expression"]["id"] == 18)
        url = "http://127.0.0.1:%d/api/status" % runtime.server.server_port
        with urllib.request.urlopen(url, timeout=2) as response:
            status = json.load(response)
        self.assertEqual(status["patchx"]["processed_messages"], 1)
        self.assertEqual(status["patchx"]["expression"]["id"], 18)
        self.assertTrue(status["connected"])
        release.set()
        self.assertTrue(cloud.done.wait(3))
        self.assertIsNone(cloud.failure)

    def test_demo_lifecycle_and_http_real_event_message(self):
        runtime = entry.create_runtime(validate_product({}), http_address=("127.0.0.1", 0))
        runtime.start()
        try:
            runtime.core.action("connect")
            runtime.core.post("message", mcp("expression_control", expression_type=18), runtime.core.generation)
            runtime.core.action("unmute")
            url = "http://127.0.0.1:%d/api/status" % runtime.server.server_port
            with urllib.request.urlopen(url, timeout=2) as response:
                state = json.load(response)
            self.assertEqual(state["patchx"]["expression"]["id"], 18)
            self.assertIsNone(runtime.core.audio)
            self.assertIsNone(runtime.business.backlight)
        finally:
            runtime.close()
        self.assertFalse(runtime.core.thread.is_alive())
        self.assertFalse(runtime.worker.is_alive())
        runtime.close()

    def test_cli_demo_really_serves_and_terminates_cleanly(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "demo.json"
            path.write_text(json.dumps({"voice": {"mode": "demo", "port": port}, "patchx": {}}))
            process = subprocess.Popen([sys.executable, "-m", "apps.patchx", "--config", str(path)],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                def ready():
                    if process.poll() is not None:
                        self.fail("product exited before HTTP startup")
                    try:
                        with urllib.request.urlopen("http://127.0.0.1:%d/api/status" % port, timeout=.2) as response:
                            state = json.load(response)
                        return state["capabilities"]["mode"] == "demo" and state["muted"]
                    except OSError:
                        return False
                wait_for(ready)
            finally:
                process.terminate()
                stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertIn("PatchX:", stdout)
            self.assertEqual(stderr, "")

    def test_invalid_live_config_is_rejected_before_server_or_audio(self):
        with self.assertRaises(ValueError):
            entry.create_runtime({"voice": {"mode": "live", "backend_url": "wss://example.test"}, "patchx": {}})

    def test_embedding_address_still_rejects_non_loopback_and_invalid_port(self):
        for address in (("0.0.0.0", 0), ("127.0.0.1", -1), ("127.0.0.1", True)):
            with self.assertRaises(ValueError):
                entry.create_runtime(validate_product({}), http_address=address)


if __name__ == "__main__":
    unittest.main()
