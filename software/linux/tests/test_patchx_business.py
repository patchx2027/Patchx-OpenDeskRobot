"""Business through the real bounded event worker, and backlight recovery."""
import importlib
import pathlib
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from apps.patchx.config import validate_product
from test_patchx_protocol import mcp
from test_patchx_support import FakeAudio, FakeCodec, FakeTransport, wait_for
try:
    from apps.patchx.business import PatchXBusiness
    from apps.patchx.product import PatchXCompanion
    from apps.patchx.devices import LinuxBacklight
except ModuleNotFoundError:
    PatchXBusiness = PatchXCompanion = None
    LinuxBacklight = object


class Port:
    def __init__(self):
        self.calls = []
        self.threads = []
        self.value = 10
    def snapshot(self):
        return {"available": True, "raw": self.value, "actual": self.value, "maximum": 255}
    def adjust(self, command):
        self.calls.append(command)
        self.threads.append(threading.current_thread().name)
        self.value = command
        return dict(self.snapshot(), ok=True, status="applied", uncertain=False)


class BusinessTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(PatchXBusiness, "PatchX event dispatcher has not been migrated")
        self.port = Port()
        self.business = PatchXBusiness(backlight=self.port)
        config = validate_product({"voice": {"mode": "live", "backend_url": "wss://example.test",
                                  "device_id": "test-device", "client_id": "test-client"},
                                   "patchx": {"client_ip": "192.0.2.8"}})["voice"]
        self.core = PatchXCompanion(config, self.business, audio_factory=FakeAudio,
                                   codec_factory=FakeCodec, transport_factory=FakeTransport)
        self.core.start()
        self.core.action("connect")
        wait_for(lambda: self.core.snapshot()["connected"])
    def tearDown(self):
        if getattr(self, "core", None):
            self.core.close()
    def message(self, value):
        self.core.transport.message(value)
    def barrier(self):
        self.core.action("unmute")

    def test_two_original_mcp_messages_execute_in_companion_event_worker(self):
        self.message(mcp("set_brightness", brightness=30))
        self.message(mcp("adjust_brightness", adjustment=-10))
        self.barrier()
        self.assertEqual(self.port.calls, [30, -10])
        self.assertEqual(self.port.threads, ["companion", "companion"])
        self.assertEqual(self.core.snapshot()["patchx"]["brightness"]["raw"], -10)

    def test_raw_expression_id_is_preserved_without_claiming_render(self):
        self.message(mcp("expression_control", expression_type=18))
        self.barrier()
        expression = self.core.snapshot()["patchx"]["expression"]
        self.assertEqual(expression["id"], 18)
        self.assertFalse(expression["renderer_available"])
        self.assertFalse(expression["rendered"])

    def test_greetings_and_emoji_preserve_intent_without_motor_io(self):
        for source, expected in (({"type": "welcome"}, 5), ({"type": "goodbye"}, 10),
                                 ({"type": "emoji", "emoji_map": "18"}, 18)):
            self.message(source)
            self.barrier()
            state = self.core.snapshot()["patchx"]
            self.assertEqual(state["movement"]["requested_id"], expected)
            self.assertEqual(state["expression"]["id"], expected)
            self.assertFalse(state["movement"]["executed"])
            self.assertEqual(state["last_result"]["status"], "unavailable")
        self.assertEqual(self.port.calls, [])

    def test_camera_volume_and_action_unavailable_is_explicit(self):
        for message, port in ((mcp("vision_recognition", query="看一看"), "camera"),
                              (mcp("set_volume", volume=30), "volume"),
                              (mcp("action_control", action_type=25), "movement")):
            self.message(message)
            self.barrier()
            state = self.core.snapshot()["patchx"]
            self.assertFalse(state[port]["available"])
            self.assertEqual(state["last_result"]["status"], "unavailable")
        self.assertEqual(self.port.calls, [])

    def test_malformed_message_does_not_kill_worker(self):
        self.message(mcp("set_brightness", brightness=True))
        self.barrier()
        self.assertEqual(self.core.snapshot()["patchx"]["rejected_messages"], 1)
        self.assertEqual(self.port.calls, [])
        self.message(mcp("set_brightness", brightness=20))
        self.barrier()
        self.assertTrue(self.core.thread.is_alive())
        self.assertEqual(self.port.calls, [20])
        with self.core.lock:
            self.core.data["reply"] = "voice state still works"
        self.assertEqual(self.core.snapshot()["reply"], "voice state still works")

    def test_device_exception_is_observable_and_worker_accepts_next_message(self):
        class FailingOncePort(Port):
            def adjust(self, command):
                if command == 30:
                    raise OSError("private fixture path")
                return super().adjust(command)
        self.business.backlight = FailingOncePort()
        self.message(mcp("set_brightness", brightness=30))
        self.barrier()
        result = self.core.snapshot()["patchx"]["last_result"]
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["uncertain"])
        self.assertNotIn("private", result["error"])
        self.message(mcp("set_brightness", brightness=20))
        self.barrier()
        self.assertEqual(self.core.snapshot()["patchx"]["brightness"]["raw"], 20)
        self.assertTrue(self.core.thread.is_alive())

    def test_old_connection_callback_cannot_write_after_reconnect(self):
        old = self.core.transport
        self.core.action("disconnect")
        self.core.action("connect")
        wait_for(lambda: self.core.snapshot()["connected"])
        old.message(mcp("set_brightness", brightness=100))
        self.barrier()
        self.assertEqual(self.port.calls, [])
        self.message(mcp("set_brightness", brightness=30))
        self.barrier()
        self.assertEqual(self.port.calls, [30])

    def test_queue_overflow_invalidates_already_queued_business(self):
        self.core.close()
        self.core.stopping.clear()
        old_generation = self.core.generation
        for _ in range(48):
            self.core.post("message", mcp("set_brightness", brightness=100), old_generation)
        self.core.post("message", mcp("set_brightness", brightness=100), old_generation)
        self.assertTrue(self.core.overflow.is_set())
        self.core.start()
        wait_for(lambda: self.core.snapshot()["metrics"]["queue_overflows"] == 1)
        self.core.action("unmute")
        self.assertEqual(self.port.calls, [])

    def test_current_generation_prehello_business_cannot_write_port(self):
        class QuietTransport(FakeTransport):
            def connect(self):
                pass
        self.core.action("disconnect")
        self.core.transport_factory = QuietTransport
        self.core.action("connect")
        self.assertFalse(self.core.snapshot()["connected"])
        self.assertEqual(self.core.snapshot()["state"], "connecting")
        self.message(mcp("set_brightness", brightness=100))
        self.barrier()
        self.assertEqual(self.port.calls, [])
        self.assertEqual(self.core.snapshot()["patchx"]["processed_messages"], 0)
        self.message({"type": "hello", "session_id": "fresh", "audio_params": {
            "format": "opus", "channels": 1, "sample_rate": 24000, "frame_duration": 60}})
        self.barrier()
        self.assertTrue(self.core.snapshot()["connected"])
        self.message(mcp("set_brightness", brightness=30))
        self.barrier()
        self.assertEqual(self.port.calls, [30])

    def test_current_generation_offline_business_cannot_write_port(self):
        self.core.action("disconnect")
        generation = self.core.generation
        self.core.post("message", mcp("set_brightness", brightness=100), generation)
        self.barrier()
        self.assertEqual(self.port.calls, [])
        self.assertEqual(self.core.snapshot()["patchx"]["processed_messages"], 0)

    def test_wrong_session_business_ignored_but_optional_and_matching_session_work(self):
        for session in ("other-session", {}, []):
            self.message(dict(mcp("set_brightness", brightness=100), session_id=session))
        self.barrier()
        self.assertEqual(self.port.calls, [])
        self.assertEqual(self.core.snapshot()["patchx"]["processed_messages"], 0)
        self.message(mcp("set_brightness", brightness=30))
        self.message(dict(mcp("adjust_brightness", adjustment=-10), session_id="fixture"))
        self.barrier()
        self.assertEqual(self.port.calls, [30, -10])
        self.assertEqual(self.core.snapshot()["patchx"]["processed_messages"], 2)

    def test_volume_port_gets_same_relative_commands(self):
        volume = Port()
        self.business.volume = volume
        self.message(mcp("set_volume", volume=30))
        self.message(mcp("adjust_volume", adjustment=-10))
        self.barrier()
        self.assertEqual(volume.calls, [30, -10])


class MirrorBacklight(LinuxBacklight):
    """Regular-file fixture emulates actual_brightness, with real file I/O."""
    def __init__(self, path):
        super().__init__(path)
        self.corrupt_actual = False
        self.read_fault = False
        self.rollback_fault = False
        self.writes = []
    def _write_value(self, value):
        self.writes.append(value)
        if self.rollback_fault and value == 100:
            raise OSError("fixture rollback failed")
        super()._write_value(value)
        (self.path / "actual_brightness").write_text(str(value if not self.corrupt_actual or value == 100 else value - 1))
    def _read_state(self):
        if self.read_fault and self.writes and self.writes[-1] != 100:
            self.read_fault = False
            raise OSError("fixture read failed after write")
        return super()._read_state()


class BacklightTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNot(LinuxBacklight, object, "Linux backlight port has not been migrated")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = pathlib.Path(self.directory.name)
        for name, value in (("brightness", 100), ("actual_brightness", 100), ("max_brightness", 255)):
            (self.path / name).write_text(str(value))
        self.device = MirrorBacklight(str(self.path))

    def test_success_uses_java_raw_percentage_and_both_readbacks(self):
        result = self.device.adjust(30)
        self.assertTrue(result["ok"])
        self.assertEqual(result["raw"], 177)
        self.assertEqual(result["actual"], 177)
        self.assertFalse(result["uncertain"])
        self.assertEqual(self.device.snapshot()["maximum"], 255)

    def test_both_endpoints_and_invalid_type(self):
        self.assertEqual(self.device.adjust(0)["raw"], 0)
        self.assertEqual(self.device.adjust(100)["raw"], 255)
        self.device.writes.clear()
        with self.assertRaises(ValueError):
            self.device.adjust(True)
        self.assertEqual(self.device.writes, [])

    def test_actual_mismatch_rolls_back_and_preserves_first_error(self):
        self.device.corrupt_actual = True
        result = self.device.adjust(30)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["rollback_verified"])
        self.assertFalse(result["uncertain"])
        self.assertEqual(result["raw"], 100)
        self.assertEqual(self.device.writes, [177, 100])
        self.assertEqual(result["error"], "backlight verification failed")

    def test_read_after_successful_write_failure_rolls_back(self):
        self.device.read_fault = True
        result = self.device.adjust(30)
        self.assertFalse(result["ok"])
        self.assertTrue(result["rollback_verified"])
        self.assertEqual(result["raw"], 100)
        self.assertEqual(self.device.writes, [177, 100])

    def test_rollback_failure_reports_uncertain_without_hiding_first_error(self):
        self.device.corrupt_actual = True
        self.device.rollback_fault = True
        result = self.device.adjust(30)
        self.assertFalse(result["ok"])
        self.assertTrue(result["uncertain"])
        self.assertFalse(result["rollback_verified"])
        self.assertEqual(result["error"], "backlight verification failed")
        self.assertEqual(result["rollback_error"], "backlight rollback failed")

    def test_invalid_or_missing_sysfs_does_not_write(self):
        (self.path / "actual_brightness").unlink()
        result = self.device.adjust(30)
        self.assertFalse(result["ok"])
        self.assertFalse(result["write_attempted"])
        self.assertFalse(result["uncertain"])
        self.assertEqual(self.device.writes, [])
        self.assertFalse(self.device.snapshot()["available"])

    def test_oversized_or_out_of_range_reading_rejected(self):
        for value in ("x" * 1000, "-1", "256", "True"):
            (self.path / "brightness").write_text(value)
            result = self.device.adjust(30)
            self.assertFalse(result["ok"])
            self.assertEqual(self.device.writes, [])


if __name__ == "__main__":
    unittest.main()
