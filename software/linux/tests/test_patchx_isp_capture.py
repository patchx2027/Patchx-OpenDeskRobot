"""Incomplete or uncertain native receipts cannot become a product photo."""
import copy
import importlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    capture = importlib.import_module("apps.patchx.isp_capture")
except ImportError:
    capture = None


def receipts():
    supervisor = [{"phase": "final", "aiq_supervisor": 1, "first_error": "none", "first_errno": 0,
        "cleanup_errno": 0, "main_exit": 0, "main_signal": 0, "main_reaped": True,
        "descendants_cleanup_completed": True, "capabilities_verified": True, "identity_verified": True,
        "timed_out": False, "cleanup_uncertain": False, "term_attempts": 0, "kill_attempts": 0}]
    helper = [{"aiq_finite": 1, "phase": phase, "action": "return", "rc": 0, "errno": 0}
              for phase in ("init", "prepare", "start", "capture", "stop", "deinit")]
    helper.append({"aiq_finite": 1, "phase": "final", "first_error": "none", "first_rc": 0,
        "first_errno": 0, "stop_rc": 0, "deinit_returned": True, "cleanup_uncertain": False,
        "public_isp_code": 5, "mapped_sdk_isp_version": 21})
    frame = {"diagnostic": "camera-bounded-format-v3", "format_verified": True, "saved": True,
        "width": 640, "height": 480, "native_format": 842094158, "frames": 31, "warmup_frames": 30,
        "corrupt_frames": 0, "first_errno": 0, "cleanup_uncertain": False,
        "planes": [{"stride": 640, "sizeimage": 460800, "payload": 460800, "allocation": 460800, "offset": 0}],
        "timestamp_monotonic": 1, "timestamp_ns": 200, "streamoff_attempts": 1,
        "streamoff_errno": 0, "munmap_attempts": 4, "munmap_errno": 0, "device_close_attempts": 2,
        "device_close_errno": 0, "output_close_errno": 0, "fd_flags_restore_errno": 0}
    return supervisor, helper, frame


class IspReceiptTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(capture, "ISP adapter is not connected to the product camera command")

    def check(self, values):
        return capture.validate_capture_receipts(*values, raw_bytes=460800, started_ns=100, now_ns=300)

    def test_all_native_success_and_exact_frame_are_required(self):
        result = self.check(receipts())
        self.assertEqual(result["captured_monotonic_ns"], 200)
        self.assertFalse(result["physical_image_quality_verified"])

    def test_uncertain_cleanup_signals_missing_sdk_and_short_frame_rejected(self):
        for group, field, value in ((0, "main_exit", 1), (0, "main_reaped", False),
                (0, "term_attempts", 1), (0, "kill_attempts", 1), (0, "cleanup_uncertain", True),
                (0, "timed_out", True), (0, "main_exit", False),
                (1, "deinit_returned", False), (1, "stop_rc", -1), (1, "cleanup_uncertain", True),
                (2, "width", 1280), (2, "timestamp_ns", 99), (2, "timestamp_ns", 301),
                (2, "timestamp_monotonic", 0), (2, "streamoff_errno", 5), (2, "saved", False)):
            values = receipts()
            target = values[group][-1] if group < 2 else values[group]
            target[field] = value
            with self.subTest(group=group, field=field), self.assertRaises(ValueError):
                self.check(values)
        values = receipts()
        values[1].pop(3)  # Missing capture return, even if final claims success.
        with self.assertRaises(ValueError):
            self.check(values)
        values = receipts()
        values[2]["planes"][0]["payload"] -= 1
        with self.assertRaises(ValueError):
            self.check(values)
        with self.assertRaises(ValueError):
            capture.validate_capture_receipts(*receipts(), raw_bytes=460799, started_ns=100, now_ns=300)


if __name__ == "__main__":
    unittest.main()
