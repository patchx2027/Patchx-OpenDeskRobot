"""Synthetic fb ioctl identity with actual file-backed mmap and failure paths."""
import errno
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    display = importlib.import_module("apps.patchx.display")
except ModuleNotFoundError:
    display = None

WIDTH, HEIGHT, STRIDE = 720, 720, 2880
SIZE = STRIDE * HEIGHT
ORIGINAL = bytes(range(256)) * (SIZE // 256)


def wait_for(predicate, timeout=3):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("bounded display wait expired")


class Mapping:
    """Inject only writes/failures into a real mmap, never a fake pixel store."""
    def __init__(self, actual):
        self.actual = actual
        self.partial_once = False
        self.partial_when_changed = False
        self.partial_changed = False
        self.fail_restore = False
        self.block_once = False
        self.entered, self.release = threading.Event(), threading.Event()

    def __len__(self):
        return len(self.actual)

    def __getitem__(self, key):
        return self.actual[key]

    def __setitem__(self, key, data):
        if self.block_once:
            self.block_once = False
            self.entered.set()
            if not self.release.wait(5):
                raise OSError("fixture write timed out")
        if self.fail_restore:
            raise OSError("SECRET fixture restore failed")
        if self.partial_once:
            start = key.start or 0
            prefix = data[:len(data) // 2]
            before = self.actual[start:start + len(prefix)]
            if self.partial_when_changed and before == prefix:
                self.actual[key] = data
                return
            self.partial_once = False
            self.actual[start:start + len(prefix)] = prefix
            self.partial_changed = before != self.actual[start:start + len(prefix)]
            raise OSError("SECRET fixture partial write")
        self.actual[key] = data

    def close(self):
        self.actual.close()


class DisplayTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(display, "Linux framebuffer display leaf is absent")
        self.tmp = tempfile.TemporaryDirectory(prefix="patchx-display-")
        self.root = Path(self.tmp.name)
        self.device = self.root / "fb0"
        self.device.write_bytes(ORIGINAL)
        self.actual_fstat, self.actual_ioctl = display.os.fstat, display.fcntl.ioctl
        self.actual_mmap = display.mmap.mmap
        self.target_stat = self.device.stat()
        self.fixed, self.variable = bytearray(80), bytearray(160)
        self.fixed[:14] = b"rockchipdrmfb\0"
        for offset, value in ((24, SIZE), (28, 0), (36, 2), (48, STRIDE)):
            struct.pack_into("=I", self.fixed, offset, value)
        values = [720, 720, 720, 720, 0, 0, 32, 0,
                  16, 8, 0, 8, 8, 0, 0, 8, 0, 24, 8, 0] + [0] * 20
        self.variable[:] = struct.pack("=40I", *values)
        self.services, self.mappings, self.opened = [], [], []
        self.map_options = {}
        self.stat_character = True
        self.ioctl_error = False
        self.short_mapping = False
        self.patchers = [mock.patch.object(display.os, "fstat", side_effect=self.fstat),
                         mock.patch.object(display.fcntl, "ioctl", side_effect=self.ioctl),
                         mock.patch.object(display.mmap, "mmap", side_effect=self.mapping)]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for mapping in self.mappings:
            mapping.release.set()
        for service in self.services:
            service.close()
            self.assertFalse(service.snapshot()["thread_alive"])
        for mapping in self.mappings:
            if not mapping.actual.closed:
                mapping.actual.close()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tmp.cleanup()

    def ours(self, result):
        return (result.st_dev, result.st_ino) == (self.target_stat.st_dev, self.target_stat.st_ino)

    def fstat(self, descriptor):
        result = self.actual_fstat(descriptor)
        if self.ours(result) and self.stat_character:
            return SimpleNamespace(st_mode=stat.S_IFCHR | stat.S_IMODE(result.st_mode),
                                   st_size=result.st_size, st_dev=result.st_dev, st_ino=result.st_ino)
        return result

    def ioctl(self, descriptor, command, buffer, mutate=True):
        if not self.ours(self.actual_fstat(descriptor)):
            return self.actual_ioctl(descriptor, command, buffer, mutate)
        self.opened.append(descriptor)
        if self.ioctl_error:
            raise OSError(errno.EIO, "SECRET fixture ioctl")
        value = {0x4600: self.variable, 0x4602: self.fixed}.get(command)
        if value is None or len(buffer) != len(value):
            raise AssertionError("unexpected framebuffer ioctl")
        buffer[:] = value
        return 0

    def mapping(self, descriptor, length, *args, **kwargs):
        if self.short_mapping:
            length -= 4
        actual = self.actual_mmap(descriptor, length, *args, **kwargs)
        wrapped = Mapping(actual)
        for key, value in self.map_options.items():
            setattr(wrapped, key, value)
        self.mappings.append(wrapped)
        return wrapped

    def service(self, **kwargs):
        result = display.DisplayService(str(self.device), **kwargs)
        self.services.append(result)
        return result

    def test_constructor_and_unused_close_do_not_open_any_device(self):
        with mock.patch.object(display.os, "open") as opened:
            service = self.service()
            self.assertFalse(service.update("idle"))
            self.assertTrue(service.close())
            opened.assert_not_called()
        self.assertEqual(service.snapshot()["status"], "closed")

    def test_regular_file_and_symlink_are_rejected_before_pixel_writes(self):
        self.stat_character = False
        service = self.service()
        self.assertFalse(service.start())
        self.assertTrue(service.close())
        self.assertEqual(self.device.read_bytes(), ORIGINAL)
        alias = self.root / "symlink"
        alias.symlink_to(self.device)
        linked = display.DisplayService(str(alias))
        self.services.append(linked)
        self.assertFalse(linked.start())
        self.assertTrue(linked.close())
        self.assertEqual(self.device.read_bytes(), ORIGINAL)

    def test_unknown_layouts_are_rejected_without_drawing(self):
        cases = [("fixed", 28, 1), ("fixed", 36, 4), ("fixed", 48, STRIDE - 1),
                 ("fixed", 24, SIZE - 1), ("variable", 0, 721), ("variable", 12, 719),
                 ("variable", 16, 1), ("variable", 24, 16), ("variable", 28, 1),
                 ("variable", 32, 0), ("variable", 48, 6), ("variable", 64, 1),
                 ("variable", 72, 4), ("variable", 80, 1), ("variable", 136, 1)]
        for name, offset, value in cases:
            with self.subTest(field=name, offset=offset):
                target = getattr(self, name)
                old = target[offset:offset + 4]
                struct.pack_into("=I", target, offset, value)
                service = self.service()
                self.assertFalse(service.start())
                self.assertTrue(service.close())
                self.assertEqual(self.device.read_bytes(), ORIGINAL)
                target[offset:offset + 4] = old
        old = self.fixed[0]
        self.fixed[0] = ord("x")
        self.assertFalse(self.service().start())
        self.fixed[0] = old

    def test_actual_mmap_renders_voice_states_and_restores_every_byte(self):
        service = self.service()
        self.assertTrue(service.start())
        wait_for(lambda: service.snapshot()["frames_written"] >= 1)
        neutral = self.device.read_bytes()
        self.assertNotEqual(neutral, ORIGINAL)
        for state, emotion in (("listening", "neutral"), ("speaking", "happy"),
                               ("thinking", "neutral"), ("idle", "sad")):
            count = service.snapshot()["frames_written"]
            self.assertTrue(service.update(state, emotion))
            wait_for(lambda: service.snapshot()["frames_written"] > count)
            self.assertNotEqual(self.device.read_bytes(), neutral)
        self.assertTrue(service.close())
        self.assertEqual(self.device.read_bytes(), ORIGINAL)
        state = service.snapshot()
        self.assertTrue(state["pixels_restored"])
        self.assertTrue(state["resources_closed"])
        self.assertFalse(state["cleanup_uncertain"])
        self.assertFalse(state["thread_alive"])

    def test_unknown_expression_falls_back_and_known_ids_are_only_hints(self):
        service = self.service()
        self.assertTrue(service.start())
        wait_for(lambda: service.snapshot()["frames_written"] >= 1)
        neutral = self.device.read_bytes()
        count = service.snapshot()["frames_written"]
        self.assertTrue(service.update("speaking", "happy", 12345))
        wait_for(lambda: service.snapshot()["frames_written"] > count)
        self.assertEqual(self.device.read_bytes(), neutral)
        self.assertEqual(service.snapshot()["last_unmapped_expression_id"], 12345)
        self.assertFalse(service.snapshot()["unity_equivalent"])
        for expression, hint in ((1, "wake"), (5, "welcome"), (10, "goodbye")):
            count = service.snapshot()["frames_written"]
            self.assertTrue(service.update("idle", "neutral", expression))
            wait_for(lambda: service.snapshot()["frames_written"] > count)
            self.assertEqual(service.snapshot()["expression_hint"], hint)
        self.assertEqual(service.snapshot()["last_unmapped_expression_id"], 12345)

    def test_latest_pending_queue_is_bounded_and_update_does_not_wait_for_render(self):
        self.map_options["block_once"] = True
        service = self.service()
        self.assertTrue(service.start())
        mapping = self.mappings[0]
        self.assertTrue(mapping.entered.wait(1))
        began = time.monotonic()
        for index in range(200):
            self.assertTrue(service.update("listening" if index % 2 else "thinking", "neutral"))
            self.assertLessEqual(service.snapshot()["pending"], 1)
        self.assertLess(time.monotonic() - began, .5)
        self.assertTrue(service.update("speaking", "happy", 5))
        self.assertEqual(service.snapshot()["expression_id"], 5)
        self.assertIsNone(service.snapshot().get("rendered_expression_id"))
        with service._condition:
            began = time.monotonic()
            self.assertFalse(service.update("idle"))
            self.assertLess(time.monotonic() - began, .1)
        mapping.release.set()
        wait_for(lambda: service.snapshot()["rendered_state"] == "idle" and
                 service.snapshot()["rendered_emotion"] == "happy")
        self.assertEqual(service.snapshot().get("rendered_expression_id"), 5)
        self.assertLessEqual(service.snapshot()["frames_written"], 3)

    def test_failed_new_expression_does_not_advance_last_completed_expression(self):
        service = self.service()
        self.assertTrue(service.start())
        wait_for(lambda: service.snapshot()["frames_written"] >= 1)
        count = service.snapshot()["frames_written"]
        self.assertTrue(service.update("idle", "neutral", 1))
        wait_for(lambda: service.snapshot()["frames_written"] > count)
        self.assertEqual(service.snapshot().get("rendered_expression_id"), 1)
        count = service.snapshot()["frames_written"]
        self.mappings[0].partial_once = True
        self.mappings[0].partial_when_changed = True
        self.assertTrue(service.update("idle", "neutral", 5))
        wait_for(lambda: service.snapshot()["status"] == "failed")
        self.assertTrue(self.mappings[0].partial_changed)
        self.assertEqual(service.snapshot()["expression_id"], 5)
        self.assertEqual(service.snapshot().get("rendered_expression_id"), 1)
        self.assertEqual(service.snapshot()["frames_written"], count)
        self.assertEqual(self.device.read_bytes(), ORIGINAL)
        self.assertFalse(service.close())

    def test_short_file_and_short_real_mapping_are_rejected_before_writes(self):
        self.device.write_bytes(ORIGINAL[:-4])
        service = self.service()
        self.assertFalse(service.start())
        self.assertEqual(self.device.read_bytes(), ORIGINAL[:-4])
        self.device.write_bytes(ORIGINAL)
        self.short_mapping = True
        service = self.service()
        self.assertFalse(service.start())
        self.assertTrue(service.close())
        self.assertEqual(self.device.read_bytes(), ORIGINAL)

    def test_partial_write_failure_restores_real_pixels_but_reports_failure(self):
        self.map_options["partial_once"] = True
        service = self.service()
        service.start()
        wait_for(lambda: service.snapshot()["status"] == "failed")
        self.assertTrue(self.mappings[0].partial_changed)
        self.assertEqual(self.device.read_bytes(), ORIGINAL)
        self.assertFalse(service.close())
        self.assertTrue(service.snapshot()["pixels_restored"])
        self.assertTrue(service.snapshot()["cleanup_uncertain"])
        self.assertFalse(service.update("idle"))
        self.assertNotIn("SECRET", service.snapshot()["error"])

    def test_restore_failure_retains_backup_fact_and_never_reports_closed(self):
        service = self.service()
        self.assertTrue(service.start())
        wait_for(lambda: service.snapshot()["frames_written"] >= 1)
        self.mappings[0].fail_restore = True
        self.assertFalse(service.close())
        state = service.snapshot()
        self.assertEqual(state["status"], "failed")
        self.assertFalse(state["pixels_restored"])
        self.assertTrue(state["backup_retained"])
        self.assertTrue(state["cleanup_uncertain"])
        self.assertFalse(state["thread_alive"])
        self.assertNotEqual(self.device.read_bytes(), ORIGINAL)
        self.assertNotIn("SECRET", repr(state))

    def test_busy_close_is_bounded_and_does_not_claim_closed(self):
        self.map_options["block_once"] = True
        service = self.service(close_timeout_s=.05)
        self.assertTrue(service.start())
        mapping = self.mappings[0]
        self.assertTrue(mapping.entered.wait(1))
        began = time.monotonic()
        self.assertFalse(service.close())
        self.assertLess(time.monotonic() - began, .5)
        self.assertTrue(service.snapshot()["thread_alive"])
        self.assertNotEqual(service.snapshot()["status"], "closed")
        mapping.release.set()
        wait_for(lambda: not service.snapshot()["thread_alive"])
        self.assertEqual(self.device.read_bytes(), ORIGINAL)
        self.assertTrue(service.snapshot()["pixels_restored"])

    def test_descriptor_close_in_progress_is_not_reported_as_released(self):
        service = self.service()
        self.assertTrue(service.start())
        wait_for(lambda: service.snapshot()["frames_written"] >= 1)
        target, actual_close = service._descriptor, display.os.close
        entered, release = threading.Event(), threading.Event()
        def close_descriptor(descriptor):
            if descriptor == target:
                entered.set()
                if not release.wait(3):
                    raise OSError("fixture close blocked")
            return actual_close(descriptor)
        closed = []
        with mock.patch.object(display.os, "close", side_effect=close_descriptor):
            closer = threading.Thread(target=lambda: closed.append(service.close()))
            closer.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(service.snapshot()["thread_alive"])
                self.assertFalse(service.snapshot()["resources_closed"])
            finally:
                release.set()
                closer.join(3)
        self.assertEqual(closed, [True])
        self.assertTrue(service.snapshot()["resources_closed"])

    def test_ioctl_failure_releases_actual_descriptor_and_sanitizes_error(self):
        self.ioctl_error = True
        service = self.service()
        self.assertFalse(service.start())
        self.assertTrue(service.close())
        self.assertTrue(self.opened)
        with self.assertRaises(OSError):
            self.actual_fstat(self.opened[0])
        self.assertNotIn("SECRET", repr(service.snapshot()))
        self.assertEqual(self.device.read_bytes(), ORIGINAL)

    def test_invalid_configuration_and_inputs_are_bounded(self):
        for path in ("relative", "/dev/../dev/fb0", "\0/dev/fb0"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                display.DisplayService(path)
        for value in (False, 0, -1, float("inf"), float("nan"), 20):
            with self.subTest(timeout=value), self.assertRaises(ValueError):
                self.service(close_timeout_s=value)
        service = self.service()
        self.assertTrue(service.start())
        for expression in (True, "1", 2 ** 31):
            self.assertFalse(service.update("idle", "neutral", expression))
        self.assertFalse(service.update("x" * 100000))
        self.assertFalse(service.update("idle", "x" * 100000))
        self.assertTrue(service.update("unknown-state", "unknown-emotion"))
        self.assertTrue(service.close())
        self.assertEqual(self.device.read_bytes(), ORIGINAL)


if __name__ == "__main__":
    unittest.main()
