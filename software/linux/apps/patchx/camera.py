"""One camera worker and a fixed, finite command adapter; no shell or network."""
from dataclasses import dataclass
import hashlib
import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from typing import Optional

from . import protocol


@dataclass(frozen=True)
class CameraFrame:
    jpeg: bytes
    width: int
    height: int
    captured_monotonic_ns: int
    metadata: dict


@dataclass(frozen=True)
class CameraCompletion:
    token: object
    query: str
    frame: Optional[CameraFrame]
    error: str
    cancelled: bool


class CameraCaptureError(Exception):
    """Only fixed adapter messages are exposed; stderr and paths stay private."""


_PUBLIC_ERRORS = frozenset((
    "camera image invalid", "camera image exceeds budget", "camera dimensions invalid",
    "camera timestamp invalid", "camera output invalid", "camera receipt invalid",
    "camera receipt invalid; cleanup uncertain", "camera receipt mismatch",
    "camera budget invalid", "camera process cleanup incomplete", "camera backend busy",
    "camera cancelled", "camera timeout; cleanup uncertain", "camera work directory unavailable",
    "camera command failed; cleanup uncertain", "camera capture failed; cleanup uncertain",
    "camera temporary cleanup failed", "camera temporary cleanup failed; cleanup uncertain",
    "camera capture failed",
))


def _budget(query):
    return protocol.jpeg_budget(query)


def _jpeg_dimensions(data):
    if not isinstance(data, bytes) or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise CameraCaptureError("camera image invalid")
    index, dimensions = 2, None
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while index < len(data) - 2:
        if data[index] != 255:
            raise CameraCaptureError("camera image invalid")
        while index < len(data) and data[index] == 255:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker == 0 or marker in (0xD8, 0xD9) or index + 2 > len(data):
            raise CameraCaptureError("camera image invalid")
        length = int.from_bytes(data[index:index + 2], "big")
        if length < 2 or index + length > len(data) - 2:
            raise CameraCaptureError("camera image invalid")
        if marker in sof:
            if dimensions is not None or length < 11:
                raise CameraCaptureError("camera image invalid")
            height = int.from_bytes(data[index + 3:index + 5], "big")
            width = int.from_bytes(data[index + 5:index + 7], "big")
            components = data[index + 7]
            if data[index + 2] != 8 or components not in (1, 3) or length != 8 + 3 * components:
                raise CameraCaptureError("camera image invalid")
            if not 1 <= width <= 8192 or not 1 <= height <= 8192:
                raise CameraCaptureError("camera image invalid")
            dimensions = (width, height)
        if marker == 0xDA:
            if dimensions is None or index + length >= len(data) - 2:
                raise CameraCaptureError("camera image invalid")
            return dimensions
        index += length
    raise CameraCaptureError("camera image invalid")


def _frame_checked(frame, budget, started_ns, now_ns):
    if not isinstance(frame, CameraFrame) or not isinstance(frame.jpeg, bytes):
        raise CameraCaptureError("camera image invalid")
    if not isinstance(frame.metadata, dict):
        raise CameraCaptureError("camera output invalid")
    if frame.metadata.get("cleanup_uncertain", False) is not False:
        raise CameraCaptureError("camera receipt invalid; cleanup uncertain")
    if not 0 < len(frame.jpeg) <= budget:
        raise CameraCaptureError("camera image exceeds budget")
    if type(frame.width) is not int or type(frame.height) is not int:
        raise CameraCaptureError("camera dimensions invalid")
    if _jpeg_dimensions(frame.jpeg) != (frame.width, frame.height):
        raise CameraCaptureError("camera dimensions invalid")
    stamp = frame.captured_monotonic_ns
    if type(stamp) is not int or not started_ns <= stamp <= now_ns:
        raise CameraCaptureError("camera timestamp invalid")
    metadata = {"jpeg_bytes": len(frame.jpeg), "jpeg_sha256": hashlib.sha256(frame.jpeg).hexdigest(),
                "cleanup_uncertain": False, "physical_cleanup_verified": False}
    return CameraFrame(frame.jpeg, frame.width, frame.height, stamp, metadata)


def _regular_read(path, limit):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= limit:
            raise CameraCaptureError("camera output invalid")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        after = os.fstat(descriptor)
        if len(data) != before.st_size or before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise CameraCaptureError("camera output invalid")
        return data
    finally:
        os.close(descriptor)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CameraCaptureError("camera receipt invalid")
        result[key] = value
    return result


class CommandCameraBackend:
    """Fixed argv, isolated output, drain until deadline, then TERM/KILL/reap.

    The fixed program must supervise its descendants through its bounded
    shutdown, including children in other sessions. D-state and detached
    descendants cannot be certified clean by this adapter. It never claims
    physical camera cleanup.
    """
    REAP_GRACE_S = 1.0

    def __init__(self, program, work_dir, termination_grace_s=1.0):
        if (type(termination_grace_s) not in (int, float) or
                not math.isfinite(termination_grace_s) or not 0 < termination_grace_s <= 6):
            raise ValueError("camera termination grace must be positive and at most 6 seconds")
        try:
            program, work_dir = os.fspath(program), os.fspath(work_dir)
        except TypeError:
            raise ValueError("camera paths must be absolute") from None
        if not isinstance(program, str) or not isinstance(work_dir, str) or not os.path.isabs(program) or not os.path.isabs(work_dir):
            raise ValueError("camera paths must be absolute")
        self.program, self.work_dir = program, work_dir
        self.termination_grace_s = float(termination_grace_s)
        self.reap_grace_s = self.REAP_GRACE_S
        self.cleanup_incomplete = False
        self.cleanup_uncertain = False
        self._unreaped = None
        self._capture_lock = threading.Lock()

    def _terminate(self, process):
        self.cleanup_uncertain = True
        # A running unreaped child reserves its PID and the session/group ID.
        # Never signal a group after this leader has already been reaped.
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=self.termination_grace_s)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=self.reap_grace_s)
        except subprocess.TimeoutExpired:
            self.cleanup_incomplete = True
            self._unreaped = process

    def capture(self, query, deadline, cancel_event, max_jpeg_bytes):
        if type(max_jpeg_bytes) is not int or not 4 <= max_jpeg_bytes <= protocol.TEXT_LIMIT:
            raise CameraCaptureError("camera budget invalid")
        if self.cleanup_incomplete:
            raise CameraCaptureError("camera process cleanup incomplete")
        if not self._capture_lock.acquire(blocking=False):
            raise CameraCaptureError("camera backend busy")
        directory, process = None, None
        started_ns = time.monotonic_ns()
        try:
            if cancel_event.is_set():
                raise CameraCaptureError("camera cancelled")
            if not isinstance(deadline, (int, float)) or not math.isfinite(deadline) or deadline <= time.monotonic():
                raise CameraCaptureError("camera timeout; cleanup uncertain")
            os.makedirs(self.work_dir, mode=0o700, exist_ok=True)
            if not stat.S_ISDIR(os.lstat(self.work_dir).st_mode):
                raise CameraCaptureError("camera work directory unavailable")
            directory = tempfile.mkdtemp(prefix="capture-", dir=self.work_dir)
            process = subprocess.Popen([self.program, "--output-dir", directory,
                                        "--max-jpeg-bytes", str(max_jpeg_bytes)],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, shell=False, close_fds=True,
                                       start_new_session=True, cwd=directory)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._terminate(process)
                    raise CameraCaptureError("camera timeout; cleanup uncertain")
                try:
                    status = process.wait(timeout=min(.05, remaining))
                    break
                except subprocess.TimeoutExpired:
                    # Cancellation invalidates delivery; it does not interrupt
                    # the native stop/deinit path before the original deadline.
                    continue
            if status != 0:
                raise CameraCaptureError("camera command failed; cleanup uncertain")
            raw = _regular_read(os.path.join(directory, "result.json"), 16384)
            receipt = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
            if not isinstance(receipt, dict) or receipt.get("ok") is not True or receipt.get("cleanup_uncertain") is not False:
                raise CameraCaptureError("camera receipt invalid; cleanup uncertain")
            if type(receipt.get("width")) is not int or type(receipt.get("height")) is not int or (receipt["width"], receipt["height"]) != (640, 480):
                raise CameraCaptureError("camera dimensions invalid")
            size, digest = receipt.get("jpeg_bytes"), receipt.get("jpeg_sha256")
            if type(size) is not int or not 0 < size <= max_jpeg_bytes or not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
                raise CameraCaptureError("camera receipt invalid")
            jpeg = _regular_read(os.path.join(directory, "frame.jpg"), max_jpeg_bytes)
            if len(jpeg) != size or hashlib.sha256(jpeg).hexdigest() != digest:
                raise CameraCaptureError("camera receipt mismatch")
            if time.monotonic() > deadline:
                raise CameraCaptureError("camera timeout; cleanup uncertain")
            frame = CameraFrame(jpeg, receipt["width"], receipt["height"],
                                receipt.get("captured_monotonic_ns"), {})
            return _frame_checked(frame, max_jpeg_bytes, started_ns, time.monotonic_ns())
        except CameraCaptureError:
            raise
        except Exception:
            if process is not None and process.poll() is None:
                self._terminate(process)
            raise CameraCaptureError("camera capture failed; cleanup uncertain") from None
        finally:
            try:
                if directory and not self.cleanup_incomplete:
                    shutil.rmtree(directory)
            except OSError:
                self.cleanup_uncertain = True
                raise CameraCaptureError("camera temporary cleanup failed; cleanup uncertain") from None
            finally:
                self._capture_lock.release()


class CameraService:
    """One active capture plus one pending request; callbacks must be nonblocking.

    cancel suppresses old completions and drains the active backend. close waits
    for its deadline plus configured termination/reap grace, returning False
    if still alive. Cleanup uncertainty latches until the service is rebuilt.
    An arbitrary backend must honor deadline; Python cannot kill a stuck thread.
    """
    def __init__(self, backend, on_complete, timeout_s=45.0):
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or not .05 <= timeout_s <= 60:
            raise ValueError("camera timeout must be between 0.05 and 60 seconds")
        if not callable(getattr(backend, "capture", None)) or not callable(on_complete):
            raise ValueError("camera backend and completion callback required")
        termination_grace = getattr(backend, "termination_grace_s", 1.0)
        reap_grace = getattr(backend, "reap_grace_s", 1.0)
        for grace, maximum in ((termination_grace, 6.0), (reap_grace, 1.0)):
            if type(grace) not in (int, float) or not math.isfinite(grace) or not 0 <= grace <= maximum:
                raise ValueError("camera backend cleanup grace invalid")
        self.backend, self.on_complete, self.timeout_s = backend, on_complete, float(timeout_s)
        self._close_grace_s = float(termination_grace + reap_grace)
        self._condition = threading.Condition(threading.RLock())
        self._pending = self._active = None
        self._epoch = 0
        self._closing = False
        self._cleanup_latched = False
        self.thread = None
        self._data = {"status": "stopped", "accepted": 0, "completed": 0, "cancelled": 0,
                      "busy_rejections": 0, "error": "", "cleanup_uncertain": False}

    def start(self):
        with self._condition:
            if self._closing or self._cleanup_latched:
                return False
            if self.thread is not None:
                return self.thread.is_alive()
            self.thread = threading.Thread(target=self._run, name="patchx-camera", daemon=True)
            self._data["status"] = "idle"
            self.thread.start()
            return True

    def request(self, query, token):
        try:
            budget = _budget(query)
            if type(budget) is not int or not 4 <= budget <= protocol.TEXT_LIMIT:
                return False
        except Exception:
            return False
        with self._condition:
            if (self._active is not None and self._active["started"] and
                    time.monotonic() >= self._active["deadline"]):
                self._latch_cleanup_locked("camera timeout; cleanup uncertain")
            if self._closing or self._cleanup_latched or self.thread is None or not self.thread.is_alive():
                return False
            if self._active is not None and self._pending is not None:
                self._data["busy_rejections"] += 1
                return False
            request = (query, token, budget, self._epoch)
            if self._active is None:
                self._active = self._reserved(request)
            else:
                self._pending = request
            self._data["accepted"] += 1
            self._condition.notify()
            return True

    @staticmethod
    def _reserved(request):
        return {"request": request, "cancel": threading.Event(), "deadline": None, "started": False}

    def _latch_cleanup_locked(self, error):
        self._cleanup_latched = True
        self._data["cleanup_uncertain"] = True
        if error:
            self._data["error"] = error
        if self._pending is not None:
            self._data["cancelled"] += 1
            self._pending = None

    def _cancel_locked(self):
        self._epoch += 1
        if self._pending is not None:
            self._data["cancelled"] += 1
            self._pending = None
        if self._active is not None and not self._active["cancel"].is_set():
            self._data["cancelled"] += 1
            self._active["cancel"].set()
            if self._active["started"]:
                self._data["status"] = "draining"
            else:
                self._active = None
        self._condition.notify_all()

    def cancel(self):
        with self._condition:
            self._cancel_locked()

    def close(self):
        with self._condition:
            self._closing = True
            self._cancel_locked()
            deadline = (self._active["deadline"] if self._active and self._active["started"]
                        else time.monotonic())
            thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0, deadline - time.monotonic()) + self._close_grace_s)
        with self._condition:
            alive = thread is not None and thread.is_alive()
            incomplete = getattr(self.backend, "cleanup_incomplete", False)
            self._data["status"] = "closing" if alive else "closed"
            if alive or incomplete:
                self._latch_cleanup_locked("camera process cleanup incomplete")
            return not alive and not incomplete

    def snapshot(self):
        with self._condition:
            alive = self.thread is not None and self.thread.is_alive()
            return dict(self._data, available=alive and not self._closing and not self._cleanup_latched,
                        thread_alive=alive, busy=self._active is not None,
                        pending=int(self._pending is not None))

    def _run(self):
        while True:
            with self._condition:
                while self._active is None and not self._closing:
                    self._condition.wait()
                if self._closing:
                    return
                active = self._active
                query, token, budget, epoch = active["request"]
                cancel = active["cancel"]
                deadline = time.monotonic() + self.timeout_s
                active.update(deadline=deadline, started=True)
                self._data["status"] = "capturing"
            started_ns = time.monotonic_ns()
            frame, error = None, ""
            try:
                frame = self.backend.capture(query, deadline, cancel, budget)
                if time.monotonic() > deadline:
                    raise CameraCaptureError("camera timeout; cleanup uncertain")
                frame = _frame_checked(frame, budget, started_ns, time.monotonic_ns())
                protocol.image_message(query, frame.jpeg)
            except CameraCaptureError as failure:
                value = failure.args[0] if failure.args else None
                error = value if isinstance(value, str) and value in _PUBLIC_ERRORS else "camera capture failed"
                frame = None
            except Exception:
                error = "camera capture failed"
                frame = None
            with self._condition:
                uncertain = ("cleanup uncertain" in error or error == "camera process cleanup incomplete" or
                             getattr(self.backend, "cleanup_uncertain", False) or
                             getattr(self.backend, "cleanup_incomplete", False))
                if uncertain:
                    error = error or "camera capture failed; cleanup uncertain"
                    frame = None
                    self._latch_cleanup_locked(error)
                self._active = self._reserved(self._pending) if self._pending is not None else None
                self._pending = None
                if error:
                    self._data["error"] = error
                if self._closing or cancel.is_set() or epoch != self._epoch:
                    self._data["status"] = ("closed" if self._closing else
                                            "error" if self._cleanup_latched else "idle")
                    continue
                self._data["status"] = "error" if error else "idle"
                self._data["error"] = error
                self._data["cleanup_uncertain"] = self._cleanup_latched
                self._data["completed"] += 1
                # Serialize delivery with cancel. RLock permits a callback to
                # inspect this service. The app callback only posts an event.
                try:
                    self.on_complete(CameraCompletion(token, query, frame, error, False))
                except Exception:
                    self._data["status"] = "error"
                    self._data["error"] = "camera completion failed"
