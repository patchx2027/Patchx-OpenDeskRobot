"""Experimental timestamped software playback reference; no audio persistence."""
import audioop
import ctypes
import math
from pathlib import Path
import threading
import time
from collections import deque

RATE = 16000
BLOCK = 256
MAX_FUTURE = 5.0


class NativeBridge:
    def __init__(self, config):
        path = config.get("aec_library")
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise ValueError("AEC library must be an absolute path")
        self.library = ctypes.CDLL(path)
        lib = self.library
        pointer = ctypes.POINTER(ctypes.c_int16)
        lib.rtctrl_aec_create.argtypes = [ctypes.c_int]
        lib.rtctrl_aec_create.restype = ctypes.c_void_p
        lib.rtctrl_aec_process256.argtypes = [ctypes.c_void_p, pointer, pointer, pointer]
        lib.rtctrl_aec_process256.restype = ctypes.c_int
        lib.rtctrl_aec_destroy.argtypes = [ctypes.c_void_p]
        lib.rtctrl_aec_destroy.restype = None
        lib.rtctrl_aec_reset.argtypes = [ctypes.c_void_p]
        lib.rtctrl_aec_reset.restype = ctypes.c_int
        lib.rtctrl_aec_error.argtypes = []
        lib.rtctrl_aec_error.restype = ctypes.c_char_p
        self.context = lib.rtctrl_aec_create(int(bool(config.get("aec_enable_aes", False))))
        if not self.context:
            raise RuntimeError("AEC initialization failed")

    def process(self, mic, reference):
        if not self.context:
            raise RuntimeError("AEC closed")
        array = ctypes.c_int16 * BLOCK
        output = array()
        if self.library.rtctrl_aec_process256(self.context, array.from_buffer_copy(mic),
                                               array.from_buffer_copy(reference), output) != 0:
            raise RuntimeError("AEC processing failed")
        return bytes(output)

    def close(self):
        if self.context:
            self.library.rtctrl_aec_destroy(self.context)
            self.context = None


class EchoCanceller:
    def __init__(self, config):
        delay = config.get("aec_reference_delay_ms", 0)
        if type(delay) not in (int, float) or not math.isfinite(delay) or not 0 <= delay <= 1000:
            raise ValueError("Invalid AEC reference delay")
        self.delay = delay / 1000
        self.native = NativeBridge(config)
        self.lock = threading.RLock()
        self.closed = False
        self.reference = deque()
        self.render_end = None
        self.render_rate = None
        self.resample_state = None
        self.render_pending_byte = b""
        self.capture_next_sample = None
        self.mic_pending = bytearray()
        self.ref_pending = bytearray()
        self.output = bytearray(BLOCK * 2)
        self.stats = {"render_samples": 0, "capture_samples": 0, "missing_reference_samples": 0,
                      "processed_blocks": 0, "reference_resets": 0, "capture_discontinuities": 0,
                      "mic_energy": 0, "reference_energy": 0, "output_energy": 0}

    @staticmethod
    def _pcm(pcm):
        if not isinstance(pcm, bytes) or len(pcm) % 2:
            raise ValueError("Expected mono PCM16 bytes")

    @staticmethod
    def _stamp(value):
        stamp = time.monotonic() if value is None else value
        if type(stamp) not in (int, float) or not math.isfinite(stamp):
            raise ValueError("Invalid audio timestamp")
        return stamp

    def _open(self):
        if self.closed:
            raise RuntimeError("AEC closed")

    def render(self, pcm, rate, submitted_at=None):
        if not isinstance(pcm, bytes):
            raise ValueError("Expected playback PCM bytes")
        if type(rate) is not int or rate not in (8000, 16000, 22050, 24000, 44100, 48000):
            raise ValueError("Unsupported reference rate")
        stamp = self._stamp(submitted_at)
        with self.lock:
            self._open()
            if not pcm:
                return
            if self.render_rate != rate:
                self.reset_reference()
                self.render_rate = rate
            pcm = self.render_pending_byte + pcm
            self.render_pending_byte = pcm[-1:] if len(pcm) % 2 else b""
            pcm = pcm[:len(pcm) // 2 * 2]
            if not pcm:
                return
            # Pipe-write scheduling jitter is not a hardware clock. Preserve
            # sample continuity unless a substantial submission gap occurred.
            scheduled = stamp + self.delay
            start = self.render_end if self.render_end is not None else scheduled
            if scheduled - start > .12:
                start = scheduled
            # Check before conversion/allocation, accounting for input duration.
            if start + len(pcm) / (rate * 2) - stamp > MAX_FUTURE:
                raise ValueError("AEC reference exceeds five-second bound")
            converted, state = audioop.ratecv(pcm, 2, 1, rate, RATE, self.resample_state)
            self.resample_state = state
            self._discard_before(stamp - MAX_FUTURE)
            count = len(converted) // 2
            if count:
                self.reference.append((round(start * RATE), converted))
            self.render_end = start + count / RATE
            self.stats["render_samples"] += count
            # History and future together are bounded, even without capture.
            if sum(len(data) for _, data in self.reference) > int(MAX_FUTURE * RATE * 2):
                self.reset_reference()
                raise ValueError("AEC reference queue overflow")

    def _discard_before(self, seconds):
        sample = round(seconds * RATE)
        while self.reference and self.reference[0][0] + len(self.reference[0][1]) // 2 <= sample:
            self.reference.popleft()

    def process(self, pcm, captured_at=None):
        self._pcm(pcm)
        if len(pcm) > RATE * 2 * MAX_FUTURE:
            raise ValueError("Capture chunk too large")
        end = self._stamp(captured_at)
        with self.lock:
            self._open()
            count = len(pcm) // 2
            observed_begin = round(end * RATE) - count
            begin = self.capture_next_sample
            if begin is None:
                begin = observed_begin
            elif abs(observed_begin - begin) > round(.25 * RATE):
                # Explicit discontinuity: pending subframes belong to the old
                # clock and cannot be paired with this capture segment.
                begin = observed_begin
                self.mic_pending.clear()
                self.ref_pending.clear()
                self.output = bytearray(BLOCK * 2)
                self.stats["capture_discontinuities"] += 1
            self.capture_next_sample = begin + count
            ref = bytearray(len(pcm))
            covered = 0
            self._discard_before(begin / RATE)
            for start, data in self.reference:
                lo, hi = max(start, begin), min(start + len(data) // 2, begin + count)
                if hi > lo:
                    ref[(lo-begin)*2:(hi-begin)*2] = data[(lo-start)*2:(hi-start)*2]
                    covered += hi-lo
            self.stats["missing_reference_samples"] += count - covered
            self.stats["capture_samples"] += count
            self.stats["mic_energy"] += audioop.rms(pcm, 2) ** 2 * count
            self.stats["reference_energy"] += audioop.rms(bytes(ref), 2) ** 2 * count
            self.mic_pending.extend(pcm)
            self.ref_pending.extend(ref)
            try:
                while len(self.mic_pending) >= BLOCK * 2:
                    result = self.native.process(bytes(self.mic_pending[:BLOCK*2]), bytes(self.ref_pending[:BLOCK*2]))
                    if not isinstance(result, bytes) or len(result) != BLOCK*2:
                        raise RuntimeError("Invalid AEC output")
                    del self.mic_pending[:BLOCK*2]
                    del self.ref_pending[:BLOCK*2]
                    self.output.extend(result)
                    self.stats["processed_blocks"] += 1
            except Exception:
                self.close()
                raise
            result = bytes(self.output[:len(pcm)])
            del self.output[:len(pcm)]
            self.stats["output_energy"] += audioop.rms(result, 2) ** 2 * count
            self._discard_before(self.capture_next_sample / RATE)
            return result

    def snapshot(self):
        """Aggregate counters only; energies use integer RMS estimates."""
        with self.lock:
            return dict(self.stats)

    def reset_reference(self, interrupted_at=None):
        with self.lock:
            self._open()
            cutoff = round(self._stamp(interrupted_at) * RATE)
            # Keep already scheduled past samples for acoustic tail alignment;
            # future samples may have been dropped by the interrupted player.
            retained = deque()
            for start, data in self.reference:
                keep = min(len(data) // 2, max(0, cutoff - start))
                if keep:
                    retained.append((start, data[:keep*2]))
            self.reference = retained
            self.render_end = self.render_rate = self.resample_state = None
            self.render_pending_byte = b""
            self.stats["reference_resets"] += 1

    def close(self):
        with self.lock:
            if not self.closed:
                self.closed = True
                self.native.close()
                self.reference.clear()
                self.mic_pending.clear()
                self.ref_pending.clear()
                self.output.clear()
