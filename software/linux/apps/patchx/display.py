"""Optional Linux framebuffer face, outside the realtime control loops.

Only explicit start opens a device. One worker owns its mapping and original
pixels; updates replace one pending request. This is a Linux fallback inspired
by the companion web avatar, not a replacement of unknown Unity semantics.
"""
import fcntl
import math
import mmap
import os
import stat
import struct
import sys
import threading
import time

WIDTH, HEIGHT, STRIDE = 720, 720, 2880
FRAME_BYTES = STRIDE * HEIGHT
_FIXED = struct.Struct("@16sLIIIIHHHILIIHHH0L")
_VARIABLE = struct.Struct("@40I")
_STATES = frozenset(("offline", "connecting", "idle", "listening", "thinking",
                     "speaking", "error", "muted"))
_EMOTIONS = frozenset(("neutral", "happy", "sad", "angry", "surprised", "thinking", "calm"))
_HINTS = {1: ("wake", "listening", "neutral"),
          5: ("welcome", "idle", "happy"),
          10: ("goodbye", "offline", "neutral")}


class _DisplayError(Exception):
    pass


def _request(state, emotion, expression_id):
    if not isinstance(state, str) or len(state) > 64:
        raise ValueError("display state invalid")
    if emotion is None:
        emotion = "neutral"
    if not isinstance(emotion, str) or len(emotion) > 64:
        raise ValueError("display emotion invalid")
    if expression_id is not None and (type(expression_id) is not int or
                                      not -(2 ** 31) <= expression_id < 2 ** 31):
        raise ValueError("display expression invalid")
    state_supported, emotion_supported = state in _STATES, emotion in _EMOTIONS
    state = state if state_supported else "idle"
    emotion = emotion if emotion_supported else "neutral"
    hint, unmapped = "", False
    if expression_id is not None:
        if expression_id in _HINTS:
            hint, state, emotion = _HINTS[expression_id]
        else:
            state, emotion, unmapped = "idle", "neutral", True
    return state, emotion, expression_id, hint, unmapped, state_supported, emotion_supported


def _span(frame, y, left, right, color):
    if not 0 <= y < HEIGHT:
        return
    left, right = max(0, left), min(WIDTH, right)
    if left < right:
        begin = y * STRIDE + left * 4
        frame[begin:begin + (right - left) * 4] = color * (right - left)


def _rect(frame, x, y, width, height, color):
    for row in range(max(0, y), min(HEIGHT, y + height)):
        _span(frame, row, x, x + width, color)


def _ellipse(frame, x, y, rx, ry, color):
    for delta in range(-ry, ry + 1):
        extent = math.isqrt(rx * rx * (ry * ry - delta * delta)) // ry
        _span(frame, y + delta, x - extent, x + extent + 1, color)


def _rounded(frame, x, y, width, height, radius, color):
    radius = min(radius, width // 2, height // 2)
    _rect(frame, x + radius, y, width - 2 * radius, height, color)
    _rect(frame, x, y + radius, width, height - 2 * radius, color)
    for cx in (x + radius, x + width - radius - 1):
        for cy in (y + radius, y + height - radius - 1):
            _ellipse(frame, cx, cy, radius, radius, color)


def _pixel(red, green, blue):
    return bytes((blue, green, red, 255))


def _render(state, emotion):
    background, shell = _pixel(11, 22, 24), _pixel(10, 28, 32)
    frame = bytearray(background * (WIDTH * HEIGHT))
    _rounded(frame, 80, 180, 560, 360, 152, _pixel(55, 107, 104))
    _rounded(frame, 83, 183, 554, 354, 149, shell)
    accent = (255, 180, 150) if state == "error" else (117, 218, 196)
    if state in ("offline", "muted"):
        accent = tuple((component * 6 + base * 4) // 10
                       for component, base in zip(accent, (11, 22, 24)))
    eye = _pixel(*accent)
    eye_height = 108 if state == "listening" else 76 if state == "thinking" else 94
    shift = 14 if state == "thinking" else 0
    for x in (214, 454):
        _rounded(frame, x + shift, 347 - eye_height // 2, 52, eye_height, 26, eye)
    cheek_alpha = 6 if emotion == "happy" else 1
    cheek = _pixel(*((component * cheek_alpha + base * (10 - cheek_alpha)) // 10
                     for component, base in zip((117, 218, 196), (10, 28, 32))))
    for x in (190, 530):
        _ellipse(frame, x, 414, 34, 12, cheek)
    endpoint, control = (460, 424) if emotion == "sad" else (440, 476)
    if state == "speaking":
        endpoint, control = endpoint - 10, control - 10
    for step in range(33):
        inverse = 32 - step
        x = (inverse * inverse * 316 + 2 * inverse * step * 360 + step * step * 404) // 1024
        y = (inverse * inverse * endpoint + 2 * inverse * step * control +
             step * step * endpoint) // 1024
        _ellipse(frame, x, y, 7, 7, eye)
    heights = (8, 24, 40, 24, 8) if state in ("listening", "speaking") else (8,) * 5
    for index, height in enumerate(heights):
        _rounded(frame, 324 + index * 16, 604 - height // 2, 8, height, 4, eye)
    return frame


class DisplayService:
    """Explicit framebuffer owner with a one-request latest-wins mailbox.

    close is bounded and reports false after a write/restore/close uncertainty.
    A stuck kernel operation cannot be killed from a Python thread. The caller
    must provide exclusive framebuffer use; this leaf changes no mode/backlight.
    """
    START_TIMEOUT_S = 2.0
    FRAME_TIMEOUT_S = 1.0

    def __init__(self, device="/dev/fb0", close_timeout_s=2.0):
        try:
            device = os.fspath(device)
        except TypeError:
            raise ValueError("display path must be absolute") from None
        if (not isinstance(device, str) or "\0" in device or not os.path.isabs(device) or
                os.path.normpath(device) != device):
            raise ValueError("display path must be a normalized absolute path")
        if (type(close_timeout_s) not in (int, float) or not math.isfinite(close_timeout_s) or
                not .05 <= close_timeout_s <= 5):
            raise ValueError("display close timeout must be between 0.05 and 5 seconds")
        self.device, self.close_timeout_s = device, float(close_timeout_s)
        self._condition = threading.Condition(threading.Lock())
        self._stop, self._ready = threading.Event(), threading.Event()
        self.thread = None
        self._pending = None
        self._descriptor = self._mapping = self._saved = None
        self._validated = False
        self._close_uncertain = False
        self._descriptor_closing = False
        self._data = {"status": "stopped", "error": "", "cleanup_uncertain": False,
                      "pixels_saved": False, "pixels_restored": False,
                      "frames_written": 0, "updates": 0, "replaced_updates": 0,
                      "state": "idle", "emotion": "neutral", "rendered_state": "",
                      "rendered_emotion": "", "expression_id": None, "expression_hint": "",
                      "rendered_expression_id": None,
                      "last_unmapped_expression_id": None, "unmapped_expressions": 0,
                      "state_supported": True, "emotion_supported": True,
                      "unity_equivalent": False, "renderer": "linux-fb2d-v1"}

    def start(self):
        with self._condition:
            if self._data["status"] == "running" and not self._stop.is_set():
                return True
            if self._data["status"] != "stopped" or self._stop.is_set():
                return False
            self._data["status"] = "starting"
            self.thread = threading.Thread(target=self._run, name="patchx-display", daemon=True)
            self.thread.start()
        if not self._ready.wait(self.START_TIMEOUT_S):
            self._stop.set()
            self._failure("display startup timeout", True)
            with self._condition:
                self._condition.notify_all()
            return False
        with self._condition:
            return self._validated and not self._stop.is_set() and not self._data["error"]

    def update(self, state="idle", emotion=None, expression_id=None):
        try:
            request = _request(state, emotion, expression_id)
        except ValueError:
            return False
        if self._stop.is_set() or not self._condition.acquire(blocking=False):
            return False
        try:
            if self._stop.is_set() or self._data["status"] != "running" or self._data["cleanup_uncertain"]:
                return False
            if self._pending is not None:
                self._increment("replaced_updates")
            self._pending = request
            self._increment("updates")
            state, emotion, expression, hint, unmapped, state_ok, emotion_ok = request
            self._data.update(state=state, emotion=emotion, expression_id=expression,
                              expression_hint=hint, state_supported=state_ok, emotion_supported=emotion_ok)
            if unmapped:
                self._data["last_unmapped_expression_id"] = expression
                self._increment("unmapped_expressions")
            self._condition.notify()
            return True
        finally:
            self._condition.release()

    def _increment(self, key):
        self._data[key] = min(2 ** 31 - 1, self._data[key] + 1)

    def close(self):
        self._stop.set()
        with self._condition:
            self._pending = None
            self._condition.notify_all()
            thread = self.thread
            if thread is None:
                self._data["status"] = "closed"
                return True
            if thread.is_alive():
                self._data["status"] = "stopping"
        if thread is not threading.current_thread():
            thread.join(self.close_timeout_s)
        if thread.is_alive():
            self._failure("display close timeout", True)
            return False
        with self._condition:
            return self._resources_closed() and not self._data["cleanup_uncertain"]

    def _resources_closed(self):
        return (self._descriptor is None and self._mapping is None and
                not self._close_uncertain and not self._descriptor_closing)

    def snapshot(self):
        with self._condition:
            alive = self.thread is not None and self.thread.is_alive()
            return dict(self._data, available=self._data["status"] == "running" and
                        alive and not self._stop.is_set() and not self._data["cleanup_uncertain"],
                        pending=int(self._pending is not None), thread_alive=alive,
                        layout_verified=self._validated, resources_closed=self._resources_closed(),
                        backup_retained=self._saved is not None)

    def _failure(self, error, uncertain):
        with self._condition:
            self._data["error"] = error
            self._data["cleanup_uncertain"] |= uncertain

    def _layout(self):
        fixed, variable = bytearray(_FIXED.size), bytearray(_VARIABLE.size)
        fcntl.ioctl(self._descriptor, 0x4602, fixed, True)
        fcntl.ioctl(self._descriptor, 0x4600, variable, True)
        f, v = _FIXED.unpack(fixed), _VARIABLE.unpack(variable)
        compatible = (f[0][:13] == b"rockchipdrmfb" and f[2] >= FRAME_BYTES and
                      f[3] == 0 and f[5] == 2 and f[9] == STRIDE and
                      v[0] == v[1] == v[2] == 720 and v[3] >= 720 and
                      v[4] == v[5] == 0 and v[6] == 32 and v[7] == v[20] == v[34] == 0 and
                      tuple(v[8:17]) == (16, 8, 0, 8, 8, 0, 0, 8, 0) and
                      (v[18] == 0 or tuple(v[17:20]) == (24, 8, 0)))
        if not compatible:
            raise _DisplayError("display layout rejected")

    def _open(self):
        if sys.byteorder != "little" or struct.calcsize("@L") != 8 or _FIXED.size != 80:
            raise _DisplayError("display ABI unsupported")
        self._descriptor = os.open(self.device, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        identity = os.fstat(self._descriptor)
        if not stat.S_ISCHR(identity.st_mode):
            raise _DisplayError("display device rejected")
        if identity.st_size and identity.st_size < FRAME_BYTES:
            raise _DisplayError("display mapping too short")
        self._layout()
        self._mapping = mmap.mmap(self._descriptor, FRAME_BYTES, flags=mmap.MAP_SHARED,
                                  prot=mmap.PROT_READ | mmap.PROT_WRITE, offset=0)
        if len(self._mapping) != FRAME_BYTES:
            raise _DisplayError("display mapping too short")
        saved = bytes(self._mapping[:])
        if len(saved) != FRAME_BYTES:
            raise _DisplayError("display backup incomplete")
        self._saved = saved
        with self._condition:
            self._validated = True
            self._data["pixels_saved"] = True

    def _write(self, pixels, interruptible):
        if len(pixels) != FRAME_BYTES:
            raise _DisplayError("display pixel buffer invalid")
        deadline = time.monotonic() + self.FRAME_TIMEOUT_S
        for row in range(HEIGHT):
            if interruptible and self._stop.is_set():
                return False
            if time.monotonic() >= deadline:
                raise _DisplayError("display frame timeout")
            start = row * STRIDE
            self._mapping[start:start + STRIDE] = pixels[start:start + STRIDE]
        if bytes(self._mapping[:]) != pixels:
            raise _DisplayError("display pixel readback failed")
        return True

    def _cleanup(self):
        if self._saved is not None and self._mapping is not None:
            try:
                self._layout()
                self._write(self._saved, False)
                with self._condition:
                    self._data["pixels_restored"] = True
                    self._saved = None
            except Exception:
                self._failure("display restore failed", True)
        if self._mapping is not None:
            try:
                self._mapping.close()
                self._mapping = None
            except Exception:
                self._failure("display unmap failed", True)
        if self._descriptor is not None:
            self._descriptor_closing = True
            descriptor, self._descriptor = self._descriptor, None
            try:
                os.close(descriptor)
            except OSError:
                # Never retry an ambiguous close against a possibly reused fd.
                self._close_uncertain = True
                self._failure("display descriptor close failed", True)
            finally:
                self._descriptor_closing = False

    def _run(self):
        try:
            self._open()
            with self._condition:
                self._data["status"] = "running"
                self._pending = _request("idle", "neutral", None)
                self._ready.set()
            while True:
                with self._condition:
                    while self._pending is None and not self._stop.is_set():
                        self._condition.wait()
                    if self._stop.is_set():
                        break
                    request, self._pending = self._pending, None
                state, emotion = request[:2]
                frame = _render(state, emotion)
                self._layout()
                if not self._write(frame, True):
                    break
                with self._condition:
                    self._increment("frames_written")
                    self._data.update(rendered_state=state, rendered_emotion=emotion,
                                      rendered_expression_id=request[2])
        except Exception:
            self._stop.set()
            self._failure("display draw failed" if self._saved is not None else "display startup failed",
                          self._saved is not None)
        finally:
            self._cleanup()
            with self._condition:
                self._pending = None
                self._data["status"] = "failed" if self._data["error"] else "closed"
                self._ready.set()
                self._condition.notify_all()
