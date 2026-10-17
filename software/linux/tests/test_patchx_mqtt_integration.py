"""Real MQTT commands reach the product queue independently of voice sessions."""
import importlib
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_patchx_mqtt import LoopbackBroker, wait_for
from test_patchx_support import FakeAudio, FakeCodec, FakeTransport
from apps.patchx.mqtt import parse_command

entry = importlib.import_module("apps.patchx.__main__")


class Port:
    def __init__(self, maximum):
        self.maximum, self.raw, self.calls = maximum, 10, []
    def snapshot(self):
        return {"available": True, "raw": self.raw, "maximum": self.maximum,
                "actual": self.raw, "verified": True}
    def set_raw(self, value):
        self.calls.append(value)
        self.raw = value
        return dict(self.snapshot(), ok=True, status="applied")
    def adjust(self, value):
        raise AssertionError("MQTT raw setting must not use MCP relative algorithm")


class Audio(FakeAudio):
    def start(self, callback):
        self.recording = True
    def interrupt(self):
        pass


class MqttIntegrationTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"PX_FIX_USER": "synthetic", "PX_FIX_PASS": "synthetic"})
        env.start()
        self.addCleanup(env.stop)
        self.broker = LoopbackBroker()
        self.addCleanup(self.broker.close)
        config = {"voice": {"mode": "live", "backend_url": "ws://127.0.0.1:1",
                  "allow_insecure_ws": True, "token_env": "", "device_id": "fixture-device",
                  "client_id": "fixture-client"}, "patchx": {"client_ip": "192.0.2.8",
                  "mqtt_enabled": True, "mqtt_broker": "127.0.0.1", "mqtt_port": self.broker.port,
                  "mqtt_tls": False, "mqtt_username_env": "PX_FIX_USER", "mqtt_password_env": "PX_FIX_PASS"}}
        self.runtime = entry.create_runtime(config, audio_factory=Audio, codec_factory=FakeCodec,
                transport_factory=FakeTransport, http_address=("127.0.0.1", 0))
        self.addCleanup(self.runtime.close)
        self.brightness, self.volume = Port(255), Port(15)
        self.runtime.business.backlight = self.brightness
        self.runtime.business.volume = self.volume
        self.runtime.start()
        wait_for(lambda: self.runtime.core.snapshot()["patchx"]["mqtt"]["connected"])

    def send(self, kind, **payload):
        self.broker.send("/devices/fixture-device/command", {"type": kind, "payload": payload})

    def test_raw_settings_work_with_voice_offline_and_reject_forged_events(self):
        core = self.runtime.core
        self.assertFalse(core.snapshot()["connected"])
        self.send("brightness", value=150)
        self.send("volume", value=7)
        wait_for(lambda: self.volume.calls == [7])
        self.assertEqual(self.brightness.calls, [150])
        self.assertEqual(core.robot_status()["payload"]["brightness"], 150)
        self.assertEqual(core.robot_status()["payload"]["volume"], 7)
        core.post("message", parse_command(b'{"type":"brightness","payload":{"value":200}}'))
        core.action("unmute")
        self.assertEqual(self.brightness.calls, [150])

    def test_original_listen_modes_require_voice_session_then_match_wire_format(self):
        core = self.runtime.core
        self.send("divination", flag=1)
        wait_for(lambda: core.snapshot()["patchx"].get("mqtt_command", {}).get("kind") == "divination")
        self.assertEqual(core.snapshot()["patchx"]["mqtt_command"]["result"]["status"], "unavailable")
        core.action("connect")
        wait_for(lambda: core.snapshot()["connected"])
        self.send("decodeDreams", flag=1)
        wait_for(lambda: any(isinstance(x, dict) and x.get("type") == "listen_mode" for x in core.transport.sent))
        self.assertIn({"type": "listen_mode", "action": "start", "session_type": "dream"}, core.transport.sent)
        self.send("decodeDreams", flag=0)
        wait_for(lambda: {"type": "listen_mode", "action": "end", "session_type": ""} in core.transport.sent)

    def test_close_fences_mqtt_and_joins_product_threads(self):
        self.runtime.close()
        self.assertFalse(self.runtime.core.mqtt.snapshot()["thread_alive"])
        self.assertFalse(self.runtime.core.thread.is_alive())
        self.assertFalse(self.runtime.worker.is_alive())

    def test_special_mode_suppresses_normal_listen_until_end_and_release_resets_it(self):
        core = self.runtime.core
        core.action("connect")
        wait_for(lambda: core.snapshot()["connected"])
        self.send("divination", flag=1)
        wait_for(lambda: core.snapshot()["patchx"].get("mqtt_command", {}).get("kind") == "divination")
        core.action("unmute")
        core.action("listen")
        self.assertFalse(any(isinstance(x, dict) and x.get("type") == "listen" for x in core.transport.sent))
        core.action("disconnect")
        core.action("connect")
        wait_for(lambda: core.snapshot()["connected"])
        core.action("unmute")
        core.action("listen")
        self.assertTrue(any(isinstance(x, dict) and x.get("type") == "listen" for x in core.transport.sent))

    def test_special_end_restores_normal_listen_without_reconnect(self):
        core = self.runtime.core
        core.action("connect")
        wait_for(lambda: core.snapshot()["connected"])
        self.send("decodeDreams", flag=1)
        wait_for(lambda: core.special_listen_mode == "dream")
        self.send("decodeDreams", flag=0)
        wait_for(lambda: core.special_listen_mode == "")
        core.action("unmute")
        core.action("listen")
        self.assertTrue(any(isinstance(x, dict) and x.get("type") == "listen" for x in core.transport.sent))


if __name__ == "__main__":
    unittest.main()
