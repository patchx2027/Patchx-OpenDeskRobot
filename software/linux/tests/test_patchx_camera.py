"""Actual subprocess captures, bounded queuing, cancellation and failure paths."""
import importlib
import ctypes
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.patchx import protocol

try:
    camera = importlib.import_module("apps.patchx.camera")
except ModuleNotFoundError:
    camera = None


def synthetic_jpeg():
    # Valid baseline grayscale JPEG: 4800 uniform 8x8 blocks, DC0/AC EOB.
    def segment(marker, payload):
        return bytes([255, marker]) + struct.pack(">H", len(payload) + 2) + payload
    quant = segment(0xDB, b"\0" + bytes([1]) * 64)
    frame = segment(0xC0, bytes([8]) + struct.pack(">HH", 480, 640) + b"\1\1\x11\0")
    counts = b"\1" + bytes(15)
    huffman = segment(0xC4, b"\0" + counts + b"\0" + b"\x10" + counts + b"\0")
    scan = segment(0xDA, b"\1\1\0\0\x3f\0")
    return b"\xff\xd8" + quant + frame + huffman + scan + bytes(1200) + b"\xff\xd9"


JPEG = synthetic_jpeg()
PROGRAM = '''#!{python}
import argparse, hashlib, json, os, signal, subprocess, sys, time
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--output-dir', required=True)
p.add_argument('--max-jpeg-bytes', type=int, required=True)
a = p.parse_args()
root = Path(__file__).resolve().parent
out = Path(a.output_dir)
mode = (root / 'mode').read_text()
(root / 'started').write_text(str(os.getpid()))
with (root / 'runs.jsonl').open('a') as f:
    f.write(json.dumps({{'pid': os.getpid(), 'argv': [a.output_dir, a.max_jpeg_bytes]}}) + '\\n')
if mode == 'hang':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(.05)
if mode == 'wait':
    while not (root / 'release').exists():
        time.sleep(.005)
if mode == 'nested':
    stopping = False
    def stop_bridge(*args):
        global stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop_bridge)
    child_code = """
import os, signal, sys, time
from pathlib import Path
root = Path(sys.argv[1])
stopping = False
def stop_child(*args):
    global stopping
    stopping = True
signal.signal(signal.SIGTERM, stop_child)
(root / 'nested-child').write_text(str(os.getpid()))
while not stopping:
    time.sleep(.005)
time.sleep(1.5)
(root / 'nested-child-drained').touch()
"""
    child = subprocess.Popen([sys.executable, '-c', child_code, str(root)],
                             start_new_session=True)
    forwarded = False
    while child.poll() is None:
        if stopping and not forwarded and (root / 'nested-child').exists():
            child.send_signal(signal.SIGTERM)
            forwarded = True
            (root / 'bridge-term').touch()
        time.sleep(.005)
    (root / 'nested-drain.json').write_text(json.dumps(
        {{'bridge_pid': os.getpid(), 'child_pid': child.pid,
          'child_exit': child.returncode, 'child_reaped': True}}))
    raise SystemExit(143)
if mode == 'exit':
    print('SECRET private command /private/fixture', flush=True)
    raise SystemExit(7)
data = {jpeg}
if mode == 'empty': data = b''
if mode == 'short': data = b'\\xff\\xd8\\xff\\xd9'
if mode == 'badjpeg': data = b'not a JPEG'
if mode == 'overbudget': data = data + b'x' * a.max_jpeg_bytes
if mode == 'symlink':
    target = root / 'outside.jpg'
    target.write_bytes(data)
    (out / 'frame.jpg').symlink_to(target)
elif mode == 'fifo':
    os.mkfifo(out / 'frame.jpg')
elif mode != 'missing':
    (out / 'frame.jpg').write_bytes(data)
stamp = time.monotonic_ns()
if mode == 'stale': stamp -= 1000000000000
if mode == 'future': stamp += 1000000000000
if mode == 'boolstamp': stamp = True
result = {{'ok': True, 'cleanup_uncertain': False, 'width': 640, 'height': 480,
          'captured_monotonic_ns': stamp, 'jpeg_bytes': len(data),
          'jpeg_sha256': hashlib.sha256(data).hexdigest(), 'private': '/SECRET/command'}}
if mode == 'wrongsha': result['jpeg_sha256'] = '0' * 64
if mode == 'wrongbytes': result['jpeg_bytes'] += 1
if mode == 'wrongdimensions': result['width'] = 320
if mode == 'uncertain': result['cleanup_uncertain'] = True
if mode == 'badjson':
    (out / 'result.json').write_text('{{BAD JSON SECRET}}')
else:
    (out / 'result.json').write_text(json.dumps(result))
if mode == 'cleanup-denied':
    out.chmod(0o500)
'''


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("bounded camera wait expired")


class CameraTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(camera, "PatchX asynchronous camera adapter is absent")
        self.tmp = tempfile.TemporaryDirectory(prefix="patchx-camera-test-")
        self.root = Path(self.tmp.name)
        self.program = self.root / "producer.py"
        self.program.write_text(PROGRAM.format(python=sys.executable, jpeg=repr(JPEG)))
        self.program.chmod(0o700)
        self.work = self.root / "work"
        self.work.mkdir()
        self.mode("normal")
        self.results = []
        self.service = None
        self.backend = camera.CommandCameraBackend(str(self.program), str(self.work))

    def tearDown(self):
        if self.service:
            self.assertTrue(self.service.close())
            self.assertFalse(self.service.snapshot()["thread_alive"])
        self.tmp.cleanup()

    def mode(self, name):
        (self.root / "mode").write_text(name)

    def service_start(self, timeout=1):
        self.service = camera.CameraService(self.backend, self.results.append, timeout_s=timeout)
        self.assertTrue(self.service.start())
        return self.service

    def started(self):
        wait_for(lambda: (self.root / "started").exists() and
                 (self.root / "started").read_text().isdigit())
        return int((self.root / "started").read_text())

    def assert_no_process(self, pid):
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_real_command_validates_and_returns_real_jpeg(self):
        start = time.monotonic_ns()
        frame = self.backend.capture("看看", time.monotonic() + 1,
                                     threading.Event(), 4000)
        self.assertEqual(frame.jpeg, JPEG)
        self.assertEqual((frame.width, frame.height), (640, 480))
        self.assertGreaterEqual(frame.captured_monotonic_ns, start)
        self.assertLessEqual(frame.captured_monotonic_ns, time.monotonic_ns())
        self.assertNotIn("SECRET", repr(frame.metadata))
        self.assertEqual(list(self.work.iterdir()), [])
        self.assert_no_process(self.started())
        evidence = os.environ.get("PATCHX_CAMERA_TEST_EVIDENCE")
        if evidence:
            directory = Path(evidence)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "synthetic-frame.jpg").write_bytes(frame.jpeg)
            (directory / "synthetic-frame.json").write_text(json.dumps(frame.metadata, indent=2))

    def test_two_queries_share_one_worker_with_one_pending_then_busy(self):
        self.mode("wait")
        service = self.service_start()
        first, second = object(), object()
        self.assertTrue(service.request("vision_recognition", first))
        self.started()
        thread_id = service.thread.ident
        self.assertTrue(service.request("secondary_vision_capture", second))
        self.assertFalse(service.request("third", object()))
        self.assertEqual(service.snapshot()["pending"], 1)
        (self.root / "release").touch()
        wait_for(lambda: len(self.results) == 2)
        self.assertEqual([x.query for x in self.results], ["vision_recognition", "secondary_vision_capture"])
        self.assertIs(self.results[0].token, first)
        self.assertIs(self.results[1].token, second)
        self.assertEqual(service.thread.ident, thread_id)
        self.assertTrue(all(x.frame.jpeg == JPEG and not x.error and not x.cancelled for x in self.results))

    def test_cancel_discards_active_and_pending_without_killing_native_drain(self):
        self.mode("wait")
        service = self.service_start()
        self.assertTrue(service.request("old", object()))
        pid = self.started()
        self.assertTrue(service.request("old pending", object()))
        service.cancel()
        self.assertEqual(service.snapshot()["pending"], 0)
        os.kill(pid, 0)
        token = object()
        self.assertTrue(service.request("fresh", token))
        (self.root / "release").touch()
        wait_for(lambda: len(self.results) == 1)
        self.assertIs(self.results[0].token, token)
        self.assertEqual(self.results[0].query, "fresh")
        self.assert_no_process(pid)

    def test_close_waits_normal_drain_and_leaves_no_camera_thread(self):
        self.mode("wait")
        service = self.service_start()
        service.request("old", object())
        pid = self.started()
        timer = threading.Timer(.08, (self.root / "release").touch)
        timer.start()
        started = time.monotonic()
        self.assertTrue(service.close())
        timer.join()
        self.assertLess(time.monotonic() - started, 2)
        self.assertFalse(service.thread.is_alive())
        self.assertEqual(self.results, [])
        self.assertFalse(service.request("closed", object()))
        self.assert_no_process(pid)

    def test_invalid_output_never_becomes_a_camera_frame(self):
        for mode in ("empty", "short", "badjpeg", "overbudget", "wrongsha", "wrongbytes",
                     "wrongdimensions", "stale", "future", "boolstamp", "uncertain",
                     "badjson", "symlink", "fifo", "missing"):
            with self.subTest(mode=mode):
                self.mode(mode)
                with self.assertRaises(Exception) as raised:
                    self.backend.capture("query SECRET", time.monotonic() + 1, threading.Event(), 4000)
                self.assertNotIn("SECRET", str(raised.exception))
                self.assertNotIn(str(self.root), str(raised.exception))
                self.assertEqual(list(self.work.iterdir()), [])
                self.assert_no_process(self.started())

    def test_failed_process_is_reaped_and_failure_is_sanitized(self):
        self.mode("exit")
        service = self.service_start()
        service.request("query", object())
        wait_for(lambda: len(self.results) == 1)
        self.assertIsNone(self.results[0].frame)
        self.assertTrue(self.results[0].error)
        self.assertNotIn("SECRET", self.results[0].error)
        self.assertNotIn(str(self.root), repr(service.snapshot()))
        self.assert_no_process(self.started())

    def test_ignored_term_deadline_kills_owned_process_and_reaps(self):
        self.mode("hang")
        service = self.service_start(timeout=.12)
        start = time.monotonic()
        service.request("query", object())
        pid = self.started()
        wait_for(lambda: len(self.results) == 1, 3)
        self.assertLess(time.monotonic() - start, 3)
        self.assertIn("timeout", self.results[0].error)
        self.assertIsNone(self.results[0].frame)
        self.assertTrue(service.snapshot()["cleanup_uncertain"])
        self.assert_no_process(pid)

    def test_budget_uses_actual_json_utf8_escapes_and_base64(self):
        service = self.service_start()
        query = "汉\\\"\n" * 900
        self.assertTrue(service.request(query, object()))
        wait_for(lambda: len(self.results) == 1)
        self.assertFalse(self.results[0].error)
        runs = [json.loads(x) for x in (self.root / "runs.jsonl").read_text().splitlines()]
        budget = runs[0]["argv"][1]
        protocol.image_message(query, b"x" * budget)
        with self.assertRaises(ValueError):
            protocol.image_message(query, b"x" * (budget + 1))

    def test_exception_does_not_leak_and_worker_serves_next_request(self):
        original = self.backend
        class Once:
            def __init__(inner):
                inner.calls = 0
            def capture(inner, *args):
                inner.calls += 1
                if inner.calls == 1:
                    raise OSError("SECRET /private/command")
                return original.capture(*args)
        self.backend = Once()
        service = self.service_start()
        service.request("first", object())
        wait_for(lambda: len(self.results) == 1)
        self.assertNotIn("SECRET", self.results[0].error)
        service.request("second", object())
        wait_for(lambda: len(self.results) == 2)
        self.assertTrue(self.results[1].frame.jpeg)

    def test_two_slots_are_available_before_worker_gets_scheduled(self):
        service = self.service_start()
        with service._condition:
            self.assertTrue(service.request("first", object()))
            self.assertTrue(service.request("second", object()))
            self.assertFalse(service.request("third", object()))
            self.assertEqual(service.snapshot()["pending"], 1)
        wait_for(lambda: len(self.results) == 2)

    def test_custom_backend_error_cannot_expose_private_message(self):
        class Bad:
            def capture(inner, *args):
                raise camera.CameraCaptureError("SECRET /private/command")
        self.backend = Bad()
        service = self.service_start()
        service.request("query", object())
        wait_for(lambda: len(self.results) == 1)
        self.assertNotIn("SECRET", self.results[0].error)

    def test_cancelled_timeout_is_not_delivered_but_retains_cleanup_uncertainty(self):
        self.mode("hang")
        service = self.service_start(timeout=.12)
        service.request("query", object())
        pid = self.started()
        service.cancel()
        wait_for(lambda: not service.snapshot()["busy"], 3)
        self.assertEqual(self.results, [])
        self.assertTrue(service.snapshot()["cleanup_uncertain"])
        self.assert_no_process(pid)

    def test_backend_uncertain_frame_is_not_published(self):
        class Uncertain:
            def capture(inner, *args):
                return camera.CameraFrame(JPEG, 640, 480, time.monotonic_ns(),
                                          {"cleanup_uncertain": True})
        self.backend = Uncertain()
        service = self.service_start()
        service.request("query", object())
        wait_for(lambda: len(self.results) == 1)
        self.assertIsNone(self.results[0].frame)
        self.assertTrue(self.results[0].error)

    def test_timeout_latches_and_discards_pending_capture(self):
        self.mode("hang")
        service = self.service_start(timeout=.15)
        self.assertTrue(service.request("first", object()))
        pid = self.started()
        self.assertTrue(service.request("pending", object()))
        wait_for(lambda: len(self.results) >= 1, 3)
        self.assertFalse(service.request("after timeout", object()))
        self.assertFalse(service.snapshot()["available"])
        self.assertEqual(service.snapshot()["pending"], 0)
        self.assertTrue(service.snapshot()["cleanup_uncertain"])
        self.mode("normal")
        self.assertFalse(service.request("still latched", object()))
        self.assertEqual(len((self.root / "runs.jsonl").read_text().splitlines()), 1)
        self.assert_no_process(pid)

    def test_expired_active_deadline_rejects_requests_during_term_drain(self):
        self.mode("hang")
        service = self.service_start(timeout=.15)
        self.assertTrue(service.request("first", object()))
        self.started()
        wait_for(lambda: time.monotonic() > service._active["deadline"] + .05)
        self.assertTrue(service.snapshot()["busy"])
        self.assertFalse(service.request("during termination", object()))
        wait_for(lambda: len(self.results) == 1, 3)
        self.assertTrue(service.snapshot()["cleanup_uncertain"])

    def test_explicit_uncertain_result_latches_after_cancellation(self):
        self.mode("wait")
        original = self.backend
        class UncertainAfterDrain:
            def capture(inner, *args):
                original.capture(*args)
                raise camera.CameraCaptureError("camera capture failed; cleanup uncertain")
        self.backend = UncertainAfterDrain()
        service = self.service_start()
        self.assertTrue(service.request("first", object()))
        self.started()
        service.cancel()
        self.assertTrue(service.request("new but unsafe", object()))
        (self.root / "release").touch()
        wait_for(lambda: not service.snapshot()["busy"])
        self.assertEqual(self.results, [])
        self.assertTrue(service.snapshot()["cleanup_uncertain"])
        self.assertFalse(service.request("retry", object()))

    def test_configurable_term_grace_allows_nested_child_drain_and_close_reaps(self):
        # Adopt an orphan only in this host regression, so a failed RED cannot
        # leave a child zombie with PID1. Production does not set a subreaper.
        library = ctypes.CDLL(None, use_errno=True)
        previous = ctypes.c_int()
        self.assertEqual(library.prctl(37, ctypes.byref(previous), 0, 0, 0), 0)
        self.assertEqual(library.prctl(36, 1, 0, 0, 0), 0)
        child_pid, closer = None, None
        try:
            self.mode("nested")
            self.backend.termination_grace_s = 2.0
            self.backend.reap_grace_s = 1.0
            service = self.service_start(timeout=.25)
            self.assertTrue(service.request("query", object()))
            bridge_pid = self.started()
            child_path = self.root / "nested-child"
            wait_for(lambda: child_path.exists() and child_path.read_text().isdigit())
            child_pid = int(child_path.read_text())
            closed = []
            began = time.monotonic()
            closer = threading.Thread(target=lambda: closed.append(service.close()))
            closer.start()
            wait_for(lambda: (self.root / "bridge-term").exists())
            os.kill(bridge_pid, 0)
            os.kill(child_pid, 0)
            closer.join(4)
            self.assertFalse(closer.is_alive())
            self.assertEqual(closed, [True])
            elapsed = time.monotonic() - began
            self.assertGreater(elapsed, 1.4)
            self.assertLess(elapsed, 3.8)
            record = json.loads((self.root / "nested-drain.json").read_text())
            self.assertEqual(record["child_exit"], 0)
            self.assertTrue(record["child_reaped"])
            self.assertTrue((self.root / "nested-child-drained").exists())
            self.assert_no_process(bridge_pid)
            self.assert_no_process(child_pid)
            self.assertFalse(service.thread.is_alive())
            self.assertEqual(self.results, [])
            evidence = os.environ.get("PATCHX_CAMERA_TEST_EVIDENCE")
            if evidence:
                record.update(elapsed_s=elapsed, bridge_present_at_term=True,
                              isolated_child_present_at_term=True, close_returned=True)
                (Path(evidence) / "nested-process-verification.json").write_text(
                    json.dumps(record, indent=2) + "\n")
        finally:
            if closer is not None:
                closer.join(5)
            if child_pid is not None:
                until = time.monotonic() + 3
                while True:
                    try:
                        owned, _ = os.waitpid(child_pid, os.WNOHANG)
                    except ChildProcessError:
                        break
                    if owned:
                        break
                    if time.monotonic() >= until:
                        os.kill(child_pid, signal.SIGKILL)
                        os.waitpid(child_pid, 0)
                        break
                    time.sleep(.01)
            self.assertEqual(library.prctl(36, previous.value, 0, 0, 0), 0)

    def test_default_deadline_and_termination_configuration_are_bounded(self):
        service = camera.CameraService(self.backend, self.results.append)
        self.assertEqual(service.timeout_s, 45.0)
        for grace in (False, 0, -1, float("inf"), float("nan"), 6.001):
            with self.subTest(grace=grace), self.assertRaises(ValueError):
                camera.CommandCameraBackend(str(self.program), str(self.work),
                                            termination_grace_s=grace)
        backend = camera.CommandCameraBackend(str(self.program), str(self.work),
                                              termination_grace_s=6.0)
        self.assertEqual(backend.termination_grace_s, 6.0)
        self.assertEqual(backend.reap_grace_s, 1.0)

    def test_backend_uncertain_flag_discards_even_a_valid_returned_frame(self):
        class Flagged:
            cleanup_uncertain = False
            def capture(inner, *args):
                inner.cleanup_uncertain = True
                return camera.CameraFrame(JPEG, 640, 480, time.monotonic_ns(), {})
        self.backend = Flagged()
        service = self.service_start()
        self.assertTrue(service.request("query", object()))
        wait_for(lambda: len(self.results) == 1)
        self.assertIsNone(self.results[0].frame)
        self.assertTrue(self.results[0].error)
        self.assertFalse(service.request("retry", object()))

    def test_temporary_cleanup_failure_retains_directory_and_latches(self):
        self.mode("cleanup-denied")
        service = self.service_start()
        # A non-root host gets a real unlink EACCES from the producer's 0500
        # directory. Root bypasses DAC, so inject only that filesystem failure
        # there; process execution, output validation and retained files are real.
        injection = (mock.patch.object(camera.shutil, "rmtree", side_effect=PermissionError("denied"))
                     if os.geteuid() == 0 else None)
        if injection:
            injection.start()
        try:
            self.assertTrue(service.request("first", object()))
            wait_for(lambda: len(self.results) == 1)
            with service._condition:
                retained = list(self.work.iterdir())
                state = service.snapshot()
                accepted = service.request("retry", object())
                receipt = {"effective_uid": os.geteuid(),
                           "mechanism": "injected EACCES" if injection else "actual directory permission EACCES",
                           "retained_directories": len(retained),
                           "retained_frame": bool(retained and (retained[0] / "frame.jpg").is_file()),
                           "retained_receipt": bool(retained and (retained[0] / "result.json").is_file()),
                           "available": state["available"],
                           "cleanup_uncertain": state["cleanup_uncertain"],
                           "next_request_accepted": accepted,
                           "frame_delivered": self.results[0].frame is not None,
                           "public_error": self.results[0].error}
                evidence = os.environ.get("PATCHX_CAMERA_TEST_EVIDENCE")
                if evidence:
                    (Path(evidence) / "temporary-cleanup-verification.json").write_text(
                        json.dumps(receipt, indent=2) + "\n")
                self.assertEqual(len(retained), 1)
                self.assertTrue(receipt["retained_frame"] and receipt["retained_receipt"])
                self.assertIsNone(self.results[0].frame)
                self.assertTrue(state["cleanup_uncertain"])
                self.assertFalse(state["available"])
                self.assertFalse(accepted)
            self.assert_no_process(self.started())
            self.assertTrue(service.close())
            self.assertEqual(len(list(self.work.iterdir())), 1)
            self.assertEqual(len((self.root / "runs.jsonl").read_text().splitlines()), 1)
        finally:
            service.close()
            if injection:
                injection.stop()
            for directory in self.work.iterdir():
                directory.chmod(0o700)

    def test_invalid_configuration_and_request_are_bounded(self):
        for value in (False, 0, -1, float("inf"), float("nan"), 10000):
            with self.subTest(value=value), self.assertRaises(ValueError):
                camera.CameraService(self.backend, self.results.append, timeout_s=value)
        for program, work in (("relative", str(self.work)), (str(self.program), "relative")):
            with self.assertRaises(ValueError):
                camera.CommandCameraBackend(program, work)
        service = self.service_start()
        self.assertFalse(service.request("x" * 1000000, object()))
        self.assertFalse(service.request(None, object()))
        self.assertEqual(list(self.work.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
