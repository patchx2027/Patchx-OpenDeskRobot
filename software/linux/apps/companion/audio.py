"""Bounded ALSA/Opus I/O for the non-realtime companion process.

Requires Linux alsa-utils and libopus. No microphone is opened until start().
Callbacks run on worker threads and must return promptly. This adapter does not
enable automatic barge-in. Optional AEC keeps capture adapted during playback.
"""
import ctypes
import ctypes.util
import os
import queue
import select
import shutil
import subprocess
import threading
import time
from typing import NamedTuple

SAMPLE_RATE = 16000
FRAME_SAMPLES = 960
FRAME_BYTES = FRAME_SAMPLES * 2
MAX_PACKET_BYTES = 4096
OUTPUT_SAMPLE_RATES = (16000, 24000, 48000)


class PcmAudio(NamedTuple):
    """Owned, bounded local PCM; never a remote wire message or temporary path."""
    data: bytes
    sample_rate: int

    def validate(self):
        if (type(self.sample_rate) is not int or self.sample_rate not in (8000, 16000, 22050, 24000, 44100, 48000) or
                not isinstance(self.data, bytes) or not self.data or len(self.data) % 2 or
                len(self.data) > self.sample_rate * 2 * 45):
            raise ValueError("Invalid local PCM audio")


class PcmStreamChunk(NamedTuple):
    """Owned local PCM segment; end closes the utterance (empty end is valid)."""
    data: bytes
    sample_rate: int
    end: bool = False

    def validate(self):
        if (type(self.sample_rate) is not int or self.sample_rate not in (8000, 16000, 22050, 24000, 44100, 48000) or
                type(self.end) is not bool or not isinstance(self.data, bytes) or
                (not self.data and not self.end) or len(self.data) % 2 or
                len(self.data) > self.sample_rate * 2 * 45):
            raise ValueError("Invalid local PCM stream chunk")


class AudioError(RuntimeError):
    pass


class OpusCodec:
    """Encode mono 16 kHz/60 ms PCM16; decode to the negotiated sample rate."""

    def __init__(self, decoder_sample_rate=16000):
        if type(decoder_sample_rate) is not int or decoder_sample_rate not in OUTPUT_SAMPLE_RATES:
            raise ValueError("decoder_sample_rate must be 16000, 24000 or 48000")
        self.decoder_sample_rate = decoder_sample_rate
        path = ctypes.util.find_library("opus")
        if not path:
            raise AudioError("libopus is missing; install the target Linux libopus runtime")
        self.lib = ctypes.CDLL(path)
        lib = self.lib
        ptr = ctypes.c_void_p
        integer = ctypes.c_int
        samples = ctypes.POINTER(ctypes.c_int16)
        data = ctypes.POINTER(ctypes.c_ubyte)
        lib.opus_encoder_create.argtypes = [integer, integer, integer, ctypes.POINTER(integer)]
        lib.opus_encoder_create.restype = ptr
        lib.opus_decoder_create.argtypes = [integer, integer, ctypes.POINTER(integer)]
        lib.opus_decoder_create.restype = ptr
        lib.opus_encode.argtypes = [ptr, samples, integer, data, ctypes.c_int32]
        lib.opus_encode.restype = integer
        lib.opus_decode.argtypes = [ptr, data, ctypes.c_int32, samples, integer, integer]
        lib.opus_decode.restype = integer
        lib.opus_encoder_destroy.argtypes = [ptr]
        lib.opus_decoder_destroy.argtypes = [ptr]
        lib.opus_encoder_destroy.restype = None
        lib.opus_decoder_destroy.restype = None
        lib.opus_decoder_ctl.argtypes = [ptr, integer]
        lib.opus_decoder_ctl.restype = integer
        self.encoder = None
        self.decoder = None
        error = integer()
        self.encoder = lib.opus_encoder_create(SAMPLE_RATE, 1, 2048, ctypes.byref(error))
        if not self.encoder or error.value:
            self.close()
            raise AudioError("opus_encoder_create failed: %d" % error.value)
        self.decoder = lib.opus_decoder_create(decoder_sample_rate, 1, ctypes.byref(error))
        if not self.decoder or error.value:
            self.close()
            raise AudioError("opus_decoder_create failed: %d" % error.value)
        self._encode_lock = threading.Lock()
        self._decode_lock = threading.Lock()

    def encode(self, pcm):
        if len(pcm) != FRAME_BYTES:
            raise ValueError("Opus input must be 1920 bytes (60 ms mono PCM16 at 16 kHz)")
        with self._encode_lock:
            if not self.encoder:
                raise AudioError("Opus codec is closed")
            raw = (ctypes.c_int16 * FRAME_SAMPLES).from_buffer_copy(pcm)
            output = (ctypes.c_ubyte * MAX_PACKET_BYTES)()
            count = self.lib.opus_encode(self.encoder, raw, FRAME_SAMPLES, output, len(output))
            if count < 0:
                raise AudioError("opus_encode failed: %d" % count)
            return bytes(output[:count])

    def decode(self, packet):
        if not packet or len(packet) > MAX_PACKET_BYTES:
            raise ValueError("Opus packet must contain 1..4096 bytes")
        with self._decode_lock:
            if not self.decoder:
                raise AudioError("Opus codec is closed")
            raw = (ctypes.c_ubyte * len(packet)).from_buffer_copy(packet)
            max_samples = self.decoder_sample_rate * 120 // 1000
            output = (ctypes.c_int16 * max_samples)()  # Maximum packet duration: 120 ms.
            count = self.lib.opus_decode(self.decoder, raw, len(packet), output, max_samples, 0)
            if count < 0:
                raise AudioError("opus_decode failed: %d" % count)
            return ctypes.string_at(output, count * 2)

    def reset_decoder(self):
        with self._decode_lock:
            if self.decoder and self.lib.opus_decoder_ctl(self.decoder, 4028) != 0:
                raise AudioError("Opus decoder reset failed")

    def close(self):
        # Owner must stop workers before closing; encode/decode cannot outlive owner.
        if self.encoder:
            self.lib.opus_encoder_destroy(self.encoder)
            self.encoder = None
        if self.decoder:
            self.lib.opus_decoder_destroy(self.decoder)
            self.decoder = None


def prerequisites():
    """Read-only dependency probe; does not open ALSA devices."""
    return {"arecord": shutil.which("arecord"), "aplay": shutil.which("aplay"),
            "libopus": ctypes.util.find_library("opus")}


class AudioIO:
    def __init__(self, config, on_error=None):
        self.config = dict(config)
        self.output_sample_rate = self.config.get("output_sample_rate", SAMPLE_RATE)
        if type(self.output_sample_rate) is not int or self.output_sample_rate not in OUTPUT_SAMPLE_RATES:
            raise ValueError("output_sample_rate must be 16000, 24000 or 48000")
        limit = self.config.get("playback_queue_frames", 16)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("playback_queue_frames must be an integer in 1..200")
        for key in ("capture_device", "playback_device"):
            value = self.config.get(key, "default")
            if not isinstance(value, str) or not value or len(value) > 256 or "\x00" in value:
                raise ValueError(key + " must be a nonempty ALSA device name")
        self.on_error = on_error
        self.last_error = None
        self._queue = queue.Queue(limit)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._stop.set()
        self._capture_stop = threading.Event()
        self._capture_stop.set()
        self._generation = 0
        self._capture = None
        self._playback = None
        self._capture_thread = None
        self._capture_callback = None
        self._aec = None
        if self.config.get("aec_enabled"):
            from .aec import EchoCanceller
            self._aec = EchoCanceller(self.config)
        self._output_thread = None
        self._codec = None
        self._inflight = False
        self._play_until = 0.0
        self.first_write_monotonic = 0.0
        self._stream_open = False
        self._stream_ending = False
        self._stream_started = False
        self._stream_rate = 0
        self._stream_bytes = 0
        self._stream_deadline = 0.0

    def _command(self, capture, sample_rate=None):
        return ["arecord" if capture else "aplay", "-q", "-D",
                self.config.get("capture_device" if capture else "playback_device", "default"),
                "-t", "raw", "-f", "S16_LE", "-r",
                str(SAMPLE_RATE if capture else (sample_rate or self.output_sample_rate)), "-c", "1",
                "--buffer-time=120000", "--period-time=20000"]

    def configure_output(self, sample_rate):
        """Apply server hello output rate; interrupt an active response first."""
        if type(sample_rate) is not int or sample_rate not in OUTPUT_SAMPLE_RATES:
            raise ValueError("output_sample_rate must be 16000, 24000 or 48000")
        with self._lock:
            if sample_rate == self.output_sample_rate:
                return
            if self.playback_busy():
                raise AudioError("Interrupt playback before changing its sample rate")
            replacement = OpusCodec(decoder_sample_rate=sample_rate) if self._codec else None
            self._terminate(self._playback)
            self._playback = None
            if self._codec:
                self._codec.close()
            self._codec = replacement
            self.output_sample_rate = sample_rate
            self.config["output_sample_rate"] = sample_rate

    @staticmethod
    def _terminate(process):
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        for stream in (process.stdin, process.stdout):
            if stream:
                stream.close()

    def _ensure_started(self):
        if not self._stop.is_set():
            return
        if self._output_thread is not None:
            raise AudioError(self.last_error or "Stop failed audio session before restarting")
        missing = [name for name, path in prerequisites().items()
                   if name != "arecord" and not path]
        if missing:
            raise AudioError("Missing audio dependencies: " + ", ".join(missing))
        if self.config.get("aec_enabled") and self._aec is None:
            from .aec import EchoCanceller
            self._aec = EchoCanceller(self.config)
        self._codec = OpusCodec(decoder_sample_rate=self.output_sample_rate)
        self.last_error = None
        self._stop.clear()
        self._output_thread = threading.Thread(target=self._output,
                                               name="companion-playback", daemon=True)
        self._output_thread.start()

    def start(self, on_frame):
        """Start microphone capture; playback can run independently via play()."""
        if not callable(on_frame):
            raise ValueError("on_frame must be callable")
        with self._lock:
            if self._aec and self._capture_thread is not None and not self._capture_stop.is_set():
                self._capture_callback = on_frame
                return
            if self._capture_thread is not None:
                raise AudioError("Capture is already started; call stop_capture first")
            if not shutil.which("arecord"):
                raise AudioError("Missing audio dependency: arecord")
            self._ensure_started()
            try:
                self._capture = subprocess.Popen(self._command(True), stdin=subprocess.DEVNULL,
                                                 stdout=subprocess.PIPE, bufsize=0)
                os.set_blocking(self._capture.stdout.fileno(), False)
            except Exception:
                self._terminate(self._capture)
                self._capture = None
                raise
            self._capture_callback = on_frame
            self._capture_stop.clear()
            self._capture_thread = threading.Thread(target=self._record, args=(on_frame,),
                                                    name="companion-capture", daemon=True)
            self._capture_thread.start()

    def _fail(self, error):
        if self._stop.is_set():
            return
        self.last_error = str(error)
        self._stop.set()
        self._capture_stop.set()
        with self._lock:
            self._stream_open = self._stream_ending = self._stream_started = False
            self._stream_rate = self._stream_bytes = 0
            self._stream_deadline = 0.0
            self._inflight = False
            self._play_until = 0.0
            while not self._queue.empty():
                self._queue.get_nowait()
            self._terminate(self._capture)
            self._capture = None
            self._terminate(self._playback)
            self._playback = None
        if self.on_error:
            self.on_error(self.last_error)

    def _record(self, callback):
        try:
            process = self._capture
            fd = process.stdout.fileno()
            pending = bytearray()
            while not self._stop.is_set() and not self._capture_stop.is_set():
                if not select.select([fd], [], [], 0.1)[0]:
                    if process.poll() is not None:
                        raise AudioError("arecord exited: %s (check device/permissions)" % process.returncode)
                    continue
                chunk = os.read(fd, FRAME_BYTES - len(pending))
                if not chunk:
                    raise AudioError("arecord closed its stream (check device/permissions)")
                pending.extend(chunk)
                if len(pending) == FRAME_BYTES:
                    if not self._capture_stop.is_set():
                        with self._lock:
                            target = self._capture_callback
                        clean = self._aec.process(bytes(pending), time.monotonic()) if self._aec else bytes(pending)
                        with self._lock:
                            if target is not self._capture_callback:
                                target = None
                        if target and not self._capture_stop.is_set():
                            target(clean)
                    pending.clear()
        except Exception as error:
            if not self._capture_stop.is_set():
                self._fail(error)

    def play(self, packet):
        if not isinstance(packet, bytes) or not 1 <= len(packet) <= MAX_PACKET_BYTES:
            raise ValueError("play expects an Opus bytes packet of 1..4096 bytes")
        with self._lock:
            self._ensure_started()
            if self._stream_open:
                raise AudioError("PCM stream must finish before Opus playback")
            if not self.playback_busy():
                self.first_write_monotonic = 0.0
            try:
                self._queue.put_nowait((self._generation, packet))
            except queue.Full as error:
                raise AudioError("Playback queue is full; interrupt/reset the response") from error

    def play_pcm(self, audio):
        if not isinstance(audio, PcmAudio):
            raise ValueError("Expected local PCM audio")
        audio.validate()
        with self._lock:
            self._ensure_started()
            if self.playback_busy():
                raise AudioError("Previous playback must finish before local PCM")
            self.first_write_monotonic = 0.0
            try:
                self._queue.put_nowait((self._generation, audio))
            except queue.Full as error:
                raise AudioError("Playback queue is full") from error

    def play_pcm_stream(self, chunk):
        if not isinstance(chunk, PcmStreamChunk):
            raise ValueError("Expected local PCM stream chunk")
        chunk.validate()
        with self._lock:
            self._ensure_started()
            if self._stream_open:
                if self._stream_ending or chunk.sample_rate != self._stream_rate:
                    raise AudioError("PCM stream ended or sample rate changed")
            elif self.playback_busy():
                raise AudioError("Previous playback must finish before PCM stream")
            if self._queue.qsize() >= min(16, self._queue.maxsize):
                raise AudioError("PCM stream queue is full")
            total = (self._stream_bytes if self._stream_open else 0) + len(chunk.data)
            if total > chunk.sample_rate * 2 * 45:
                raise AudioError("PCM stream exceeds 45 seconds")
            if not self._stream_open:
                self._stream_open = True
                self._stream_started = False
                self._stream_rate = chunk.sample_rate
                self.first_write_monotonic = 0.0
            self._stream_bytes = total
            self._stream_ending = chunk.end
            self._stream_deadline = time.monotonic() + 15.0
            self._queue.put_nowait((self._generation, chunk))

    def playback_busy(self):
        """Includes 100 ms ALSA tail margin; not a hardware sample-clock query."""
        with self._lock:
            return (self._stream_open or not self._queue.empty() or self._inflight or
                    time.monotonic() < self._play_until + 0.1)

    def _output(self):
        try:
            while not self._stop.is_set():
                with self._lock:
                    try:
                        generation, packet = self._queue.get_nowait()
                    except queue.Empty:
                        packet = None
                    if (packet is None and self._stream_open and
                            time.monotonic() > max(self._stream_deadline, self._play_until + 15.0)):
                        raise AudioError("PCM stream end/chunk timed out")
                    if self._playback is not None and self._playback.poll() is not None:
                        raise AudioError("aplay exited: %s (check device/permissions)" % self._playback.returncode)
                    if packet is not None:
                        if generation != self._generation or self._stop.is_set():
                            continue
                        self._inflight = True
                        stream_pcm = isinstance(packet, PcmStreamChunk)
                        local_pcm = isinstance(packet, PcmAudio)
                        close_pcm = local_pcm or (stream_pcm and packet.end)
                        if stream_pcm:
                            packet.validate()
                            pcm, rate = packet.data, packet.sample_rate
                            if not self._stream_started:
                                self._terminate(self._playback)
                                self._playback = None
                                self._stream_started = True
                        elif local_pcm:
                            packet.validate()
                            pcm, rate = packet.data, packet.sample_rate
                            self._terminate(self._playback)
                            self._playback = None
                        else:
                            pcm, rate = self._codec.decode(packet), self.output_sample_rate
                        if self._playback is None:
                            self._playback = subprocess.Popen(self._command(False, rate),
                                                              stdin=subprocess.PIPE,
                                                              stdout=subprocess.DEVNULL, bufsize=0)
                            os.set_blocking(self._playback.stdin.fileno(), False)
                        process = self._playback
                        fd = process.stdin.fileno()
                if packet is None:
                    self._stop.wait(0.01)
                    continue
                offset = 0
                write_deadline = time.monotonic() + max(15.0, len(pcm) / (rate * 2) + 3.0)
                while offset < len(pcm) and not self._stop.is_set():
                    with self._lock:
                        if generation != self._generation:
                            break
                        if stream_pcm and time.monotonic() > write_deadline:
                            raise AudioError("PCM stream write timed out")
                        if process.poll() is not None:
                            raise AudioError("aplay exited: %s (check device/permissions)" % process.returncode)
                        if select.select([], [fd], [], 0)[1]:
                            try:
                                count = os.write(fd, pcm[offset:])
                                if count and self._aec:
                                    self._aec.render(pcm[offset:offset + count], rate, time.monotonic())
                                offset += count
                                if count and not self.first_write_monotonic:
                                    self.first_write_monotonic = time.monotonic()
                                self._play_until = max(time.monotonic(), self._play_until) + count / (rate * 2)
                            except BlockingIOError:
                                pass
                    if offset < len(pcm):
                        self._stop.wait(0.01)
                if close_pcm:
                    # EOF lets aplay drain the actual ALSA tail. Do not report
                    # completion merely because all bytes fitted in the pipe.
                    with self._lock:
                        current = generation == self._generation and not self._stop.is_set()
                        if current:
                            process.stdin.close()
                    # A large pipe can still hold several seconds at 8kHz.
                    drain_deadline = max(time.monotonic(), self._play_until) + 3
                    while current and process.poll() is None:
                        if time.monotonic() > drain_deadline:
                            raise AudioError("Local PCM playback drain timed out")
                        self._stop.wait(.01)
                        with self._lock:
                            current = generation == self._generation and not self._stop.is_set()
                    with self._lock:
                        if current:
                            if process.returncode != 0:
                                raise AudioError("Local PCM playback failed")
                            self._playback = None
                            self._play_until = 0.0
                            if stream_pcm:
                                self._stream_open = self._stream_ending = self._stream_started = False
                                self._stream_rate = self._stream_bytes = 0
                                self._stream_deadline = 0.0
                with self._lock:
                    if generation == self._generation:
                        self._inflight = False
        except Exception as error:
            self._fail(error)

    def interrupt(self):
        with self._lock:
            if self._aec:
                self._aec.reset_reference()
            self._generation += 1
            self._stream_open = self._stream_ending = self._stream_started = False
            self._stream_rate = self._stream_bytes = 0
            self._stream_deadline = 0.0
            self.first_write_monotonic = 0.0
            self._inflight = False
            self._play_until = 0.0
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
            self._terminate(self._playback)
            self._playback = None
            if self._codec:
                self._codec.reset_decoder()

    @staticmethod
    def _join(thread):
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3)
            if thread.is_alive():
                raise AudioError("Audio callback did not return; worker could not stop")

    def pause_capture(self):
        """Keep AEC adapted during playback; stop forwarding PCM to the turn."""
        if not self._aec:
            self.stop_capture()
            return
        with self._lock:
            self._capture_callback = None

    def aec_status(self):
        if not self._aec:
            return {"enabled": False}
        return {"enabled": True, "capture_active": not self._capture_stop.is_set(),
                "barge_in": False}

    def stop_capture(self):
        self._capture_callback = None
        self._capture_stop.set()
        with self._lock:
            self._terminate(self._capture)
            self._capture = None
            thread = self._capture_thread
        self._join(thread)
        self._capture_thread = None

    def stop(self):
        self._stop.set()
        self.stop_capture()
        self.interrupt()
        self._join(self._output_thread)
        self._output_thread = None
        with self._lock:
            if self._aec:
                self._aec.close()
                self._aec = None
            if self._codec:
                self._codec.close()
                self._codec = None
