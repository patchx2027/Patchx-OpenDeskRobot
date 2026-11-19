"""RK3568 PatchX audio leaf: explicit routes and logical PCM attenuation.

Construction opens nothing. Capture selects USB channel zero; playback uses
ALSA conversion to the RK809 native format. CAE, wake-word and AEC equivalence
are not provided by this leaf. Speaker ownership is injected explicitly.
"""
import array
import os
import queue
import re
import select
import struct
import subprocess
import sys
import threading
import time

from apps.companion.audio import AudioError, AudioIO, OpusCodec, OUTPUT_SAMPLE_RATES, PcmAudio, PcmStreamChunk
from .protocol import android_int, legacy_target

CAPTURE_DEVICE = "patchx_capture"
PLAYBACK_DEVICE = "patchx_playback"


class SoftwareVolume:
    """Android logical range 0..15, linear PCM amplitude; no hardware gain."""

    def __init__(self, raw=15):
        android_int(raw)
        if not 0 <= raw <= 15:
            raise ValueError("logical volume must be 0..15")
        self._raw = raw
        self._lock = threading.Lock()

    def snapshot(self):
        with self._lock:
            return {"available": True, "raw": self._raw, "maximum": 15,
                    "verified": True, "method": "software_pcm_linear",
                    "hardware_gain": False}

    def set_raw(self, value):
        android_int(value)
        if not 0 <= value <= 15:
            return dict(self.snapshot(), ok=False, status="rejected", uncertain=False)
        with self._lock:
            self._raw = value
        return dict(self.snapshot(), ok=True, status="applied", uncertain=False)

    def adjust(self, command):
        android_int(command)
        with self._lock:
            self._raw = legacy_target(self._raw, 0, 15, command)
        return dict(self.snapshot(), ok=True, status="applied", uncertain=False)

    def apply(self, pcm):
        if not isinstance(pcm, bytes) or len(pcm) % 2:
            raise ValueError("expected complete S16_LE PCM samples")
        with self._lock:
            raw = self._raw
        if raw == 15:
            return pcm
        if raw == 0:
            return bytes(len(pcm))
        samples = array.array("h")
        samples.frombytes(pcm)
        if sys.byteorder != "little":
            samples.byteswap()
        for index, sample in enumerate(samples):
            magnitude = (abs(sample) * raw + 7) // 15
            samples[index] = -magnitude if sample < 0 else magnitude
        if sys.byteorder != "little":
            samples.byteswap()
        return samples.tobytes()


class RK809SpeakerRoute:
    """Own only Playback Path; reject unexpected controls or non-OFF baseline.

    The product must verify the card/PCM identity and provide exclusive audio
    access before using this port. amixer calls have a two-second soft timeout.
    A failed write latches uncertainty even when cached OFF later reads back.
    """

    CONTROLS = {
        "Playback Path": ("OFF", "RCV", "SPK", "HP", "HP_NO_MIC", "BT", "SPK_HP", "RING_SPK", "RING_HP", "RING_HP_NO_MIC", "RING_SPK_HP"),
        "Capture MIC Path": ("MIC OFF", "Main Mic", "Hands Free Mic", "BT Sco Mic"),
        "Resume Path": ("OFF", "ON"),
    }

    def __init__(self, card_id="rockchiprk809co", runner=None, *, playback_route="HP_NO_MIC"):
        if not isinstance(card_id, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,30}", card_id):
            raise ValueError("invalid RK809 ALSA card ID")
        if playback_route not in ("SPK", "HP_NO_MIC"):
            raise ValueError("unsupported RK809 playback route")
        self.card_id = card_id
        self.playback_route = playback_route
        self._runner = runner or subprocess.run
        self._lock = threading.RLock()
        self._active = False
        self._changed = False
        self.cleanup_uncertain = False
        self.last_error = ""

    def _call(self, operation, name, value=None):
        command = ["amixer", "-c", self.card_id, operation, "name=" + name]
        if value is not None:
            command.append(value)
        result = self._runner(command, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, encoding="ascii", timeout=2, check=False)
        if result.returncode != 0 or len(result.stdout) > 8192:
            raise AudioError("RK809 control call failed")
        return result.stdout

    def _read(self, name):
        text = self._call("cget", name)
        labels = re.findall(r"^\s*; Item #([0-9]+) '([^']+)'\s*$", text, re.MULTILINE)
        expected = self.CONTROLS[name]
        if ("name='" + name + "'" not in text or
                not re.search(r"type=ENUMERATED,access=rw[^,]*,values=1,items=" + str(len(expected)) + r"(?:\s|$)", text) or
                labels != [(str(index), label) for index, label in enumerate(expected)]):
            raise AudioError("RK809 control identity or labels changed")
        match = re.search(r"^\s*: values=([0-9]+)\s*$", text, re.MULTILINE)
        if match is None or int(match.group(1)) >= len(expected):
            raise AudioError("RK809 control reading is invalid")
        return int(match.group(1))

    def _all(self):
        return {name: self._read(name) for name in self.CONTROLS}

    def acquire(self):
        with self._lock:
            if self.cleanup_uncertain:
                raise AudioError("RK809 route cleanup is uncertain")
            if self._active:
                return True
            if any(self._all().values()):
                raise AudioError("RK809 routes must all be OFF before playback")
            self._active = True
            self._changed = True
            try:
                self._call("cset", "Playback Path", self.playback_route)
                after = self._all()
                if after != {"Playback Path": self.CONTROLS["Playback Path"].index(self.playback_route), "Capture MIC Path": 0, "Resume Path": 0}:
                    raise AudioError(f"RK809 {self.playback_route} route did not verify")
                return True
            except Exception as error:
                self.cleanup_uncertain = True
                self.last_error = str(error)
                self.release()
                raise AudioError(f"RK809 {self.playback_route} route could not be established") from error

    def release(self):
        with self._lock:
            if not self._active:
                return not self.cleanup_uncertain
            self._active = False
            try:
                if self._changed:
                    self._changed = False
                    self._call("cset", "Playback Path", "OFF")
                if any(self._all().values()):
                    raise AudioError("RK809 OFF restoration did not verify")
            except Exception as error:
                self.cleanup_uncertain = True
                self.last_error = str(error)
            return not self.cleanup_uncertain

    def snapshot(self):
        with self._lock:
            return {"active": self._active, "cleanup_uncertain": self.cleanup_uncertain,
                    "error": self.last_error, "card_id": self.card_id}


class PatchXAudioIO(AudioIO):
    """Reuse the bounded companion capture/Opus contract with board ownership."""

    def __init__(self, config, on_error=None, *, speaker_route, volume=None, wake_aec_library=""):
        if not callable(getattr(speaker_route, "acquire", None)) or not callable(getattr(speaker_route, "release", None)):
            raise ValueError("an explicit speaker ownership port is required")
        board_config = dict(config)
        if board_config.get("aec_enabled"):
            raise ValueError("this basic USB route does not provide verified AEC")
        for key, default in (("capture_device", CAPTURE_DEVICE), ("playback_device", PLAYBACK_DEVICE)):
            if board_config.get(key, "default") == "default":
                board_config[key] = default
        self.capture_array = bool(wake_aec_library)
        if self.capture_array:
            board_config.update(aec_enabled=True, aec_library=wake_aec_library,
                                capture_device="patchx_capture_array")
        super().__init__(board_config, on_error)
        self.speaker_route = speaker_route
        self.volume = volume if volume is not None else SoftwareVolume()
        if not isinstance(self.volume, SoftwareVolume):
            raise ValueError("expected the logical PCM volume port")
        self.cleanup_uncertain = False
        self._speaker_owned = False
        # Lifecycle operations serialize here; never wait for this lock while
        # holding the shared packet/capture lock.
        self._playback_lock = threading.RLock()
        self._interrupting = 0
        self._reply_end = None
        self._packet_at = None
        self._first_packet_at = None
        self._queue_empty_at = None
        # Instance-lifetime numeric maxima; queue starvation is not an ALSA XRUN count.
        self._timing = dict(packets=0, max_packet_gap_ms=0.0,
                            max_queue_empty_ms=0.0, first_packet_to_write_ms=0.0,
                            max_first_packet_to_write_ms=0.0,
                            max_route_acquire_ms=0.0, max_route_release_ms=0.0)

    def _timing_max(self, name, seconds):
        self._timing[name] = max(self._timing[name], seconds * 1000.0)

    def _end_queue_empty(self, now):
        if self._queue_empty_at is not None:
            self._timing_max("max_queue_empty_ms", now - self._queue_empty_at)
            self._queue_empty_at = None

    def play(self, packet):
        # Accepted-packet gaps include dispatch/lock delays, not just network jitter.
        with self._lock:
            super().play(packet)
            now = time.monotonic()
            if self._packet_at is not None:
                self._timing_max("max_packet_gap_ms", now - self._packet_at)
            if self._packet_at is None:
                self._first_packet_at = now
            self._packet_at = now
            self._timing["packets"] += 1

    def interrupt(self):
        with self._lock:
            self._interrupting += 1
            self._end_queue_empty(time.monotonic())
            self._packet_at = self._first_packet_at = None
            self._generation += 1
            self._reply_end = None
            self._stream_open = self._stream_ending = self._stream_started = False
            self._stream_rate = self._stream_bytes = 0
            self._stream_deadline = self.first_write_monotonic = self._play_until = 0.0
            self._inflight = False
            while not self._queue.empty():
                self._queue.get_nowait()
        try:
            with self._playback_lock:
                self._terminate(self._playback)
                self._playback = None
                with self._lock:
                    if self._aec:
                        self._aec.reset_reference()
                    if self._codec:
                        self._codec.reset_decoder()
        finally:
            with self._lock:
                self._interrupting -= 1

    def configure_output(self, sample_rate):
        if type(sample_rate) is not int or sample_rate not in OUTPUT_SAMPLE_RATES:
            raise AudioError("output_sample_rate must be 16000, 24000 or 48000")
        with self._playback_lock:
            with self._lock:
                if sample_rate == self.output_sample_rate:
                    return
                if self.playback_busy():
                    raise AudioError("Interrupt playback before changing its sample rate")
                replacement = OpusCodec(decoder_sample_rate=sample_rate) if self._codec else None
            try:
                self._terminate(self._playback)
                self._playback = None
            except Exception:
                if replacement:
                    replacement.close()
                raise
            with self._lock:
                if self._codec:
                    self._codec.close()
                self._codec = replacement
                self.output_sample_rate = sample_rate
                self.config["output_sample_rate"] = sample_rate

    def _command(self, capture, sample_rate=None):
        command = super()._command(capture, sample_rate)
        if capture and self.capture_array:
            command[command.index("-c") + 1] = "8"
        return command

    def _capture_frame_bytes(self):
        return super()._capture_frame_bytes() * (8 if self.capture_array else 1)

    def _capture_pcm(self, data):
        if not self.capture_array:
            return super()._capture_pcm(data)
        microphone = bytearray()
        reference = bytearray()
        for row in struct.iter_unpack("<8h", data):
            microphone.extend(struct.pack("<h", row[0]))
            # Bothlent ref7/ref8 have opposite polarity on this mono route.
            # Keep ref7 independent so the echo reference cannot cancel itself.
            reference.extend(struct.pack("<h", row[6]))
        return self._aec.process_synchronous(bytes(microphone), bytes(reference))

    def _ensure_started(self):
        if self.cleanup_uncertain or getattr(self.speaker_route, "cleanup_uncertain", False):
            raise AudioError("audio cleanup is uncertain; refusing another session")
        super()._ensure_started()

    def _release_speaker(self):
        if not self._speaker_owned:
            return
        if self._playback is not None and (self._playback.poll() is None or not self._playback.stdin.closed):
            self.cleanup_uncertain = True
            raise AudioError("cannot restore speaker while PCM ownership remains")
        self._speaker_owned = False
        try:
            started = time.monotonic()
            try:
                if self.speaker_route.release() is not True:
                    raise AudioError("speaker route restoration did not verify")
            finally:
                self._timing_max("max_route_release_ms", time.monotonic() - started)
        except Exception as error:
            self.cleanup_uncertain = True
            self.last_error = str(error)
            raise AudioError("speaker route cleanup is uncertain") from error

    def _terminate(self, process):
        playback = process is not None and process is self._playback
        try:
            super()._terminate(process)
        except Exception:
            self.cleanup_uncertain = True
            raise
        if playback:
            self._release_speaker()

    def _open_playback(self, rate, generation):
        self._speaker_owned = True
        try:
            started = time.monotonic()
            try:
                if self.speaker_route.acquire() is not True:
                    raise AudioError("speaker route ownership was refused")
            finally:
                self._timing_max("max_route_acquire_ms", time.monotonic() - started)
            with self._lock:
                cancelled = generation != self._generation or self._stop.is_set() or self._interrupting
            if cancelled:
                self._release_speaker()
                return False
            self._playback = subprocess.Popen(self._command(False, rate), stdin=subprocess.PIPE,
                                              stdout=subprocess.DEVNULL, bufsize=0)
            os.set_blocking(self._playback.stdin.fileno(), False)
            with self._lock:
                cancelled = generation != self._generation or self._stop.is_set() or self._interrupting
            if cancelled:
                self._terminate(self._playback)
                self._playback = None
                return False
            return True
        except Exception:
            if self._playback is not None:
                self._terminate(self._playback)
                self._playback = None
            else:
                self._release_speaker()
            raise

    def _fail(self, error):
        if self._stop.is_set():
            return
        self.last_error = str(error)
        self._stop.set()
        self._capture_stop.set()
        with self._lock:
            self._end_queue_empty(time.monotonic())
            self._packet_at = self._first_packet_at = None
            self._generation += 1
            self._reply_end = None
            self._stream_open = self._stream_ending = self._stream_started = False
            self._stream_rate = self._stream_bytes = 0
            self._stream_deadline = self._play_until = 0.0
            self._inflight = False
            while not self._queue.empty():
                self._queue.get_nowait()
        with self._playback_lock:
            for name in ("_capture", "_playback"):
                process = getattr(self, name)
                try:
                    self._terminate(process)
                    setattr(self, name, None)
                except Exception:
                    self.cleanup_uncertain = True
            try:
                self._release_speaker()
            except Exception:
                self.cleanup_uncertain = True
        if self.on_error:
            self.on_error(self.last_error)

    def _output(self):
        try:
            while not self._stop.is_set():
                packet = None
                end_reply = False
                process = None
                with self._playback_lock:
                    with self._lock:
                        if self._interrupting:
                            paused = True
                        else:
                            paused = False
                            try:
                                generation, packet = self._queue.get_nowait()
                            except queue.Empty:
                                packet = None
                            now = time.monotonic()
                            if packet is not None:
                                self._end_queue_empty(now)
                            elif self._playback is not None and self._packet_at is not None:
                                if self._queue_empty_at is None:
                                    self._queue_empty_at = now
                            end_reply = packet is None and self._reply_end == self._generation
                            if end_reply:
                                self._reply_end = None
                                generation = self._generation
                                process = self._playback
                                self._inflight = process is not None
                            if (packet is None and self._stream_open and
                                    now > max(self._stream_deadline, self._play_until + 15.0)):
                                raise AudioError("PCM stream end/chunk timed out")
                            if self._playback is not None and self._playback.poll() is not None:
                                raise AudioError("aplay exited unexpectedly")
                            if packet is not None:
                                if generation != self._generation or self._stop.is_set():
                                    continue
                                self._inflight = True
                                stream_pcm = isinstance(packet, PcmStreamChunk)
                                local_pcm = isinstance(packet, PcmAudio)
                                close_pcm = local_pcm or (stream_pcm and packet.end)
                                replace = local_pcm or (stream_pcm and not self._stream_started)
                                if stream_pcm:
                                    packet.validate()
                                    pcm, rate = packet.data, packet.sample_rate
                                    self._stream_started = True
                                elif local_pcm:
                                    pcm, rate = packet.data, packet.sample_rate
                                else:
                                    pcm, rate = self._codec.decode(packet), self.output_sample_rate
                                pcm = self.volume.apply(pcm)
                    if not paused and packet is not None:
                        if replace:
                            self._terminate(self._playback)
                            self._playback = None
                        if self._playback is None and not self._open_playback(rate, generation):
                            continue
                        with self._lock:
                            if generation != self._generation or self._stop.is_set() or self._interrupting:
                                continue
                            process = self._playback
                            fd = process.stdin.fileno()
                if packet is None:
                    if end_reply and process is not None:
                        self._drain_playback(process, generation)
                        with self._lock:
                            if generation == self._generation:
                                self._inflight = False
                    self._stop.wait(.01)
                    continue
                offset = 0
                write_deadline = time.monotonic() + max(15.0, len(pcm) / (rate * 2) + 3.0)
                while offset < len(pcm) and not self._stop.is_set():
                    with self._playback_lock:
                        with self._lock:
                            if generation != self._generation or self._stop.is_set() or self._interrupting:
                                break
                            if time.monotonic() > write_deadline:
                                raise AudioError("PCM write timed out")
                            if process.poll() is not None:
                                raise AudioError("aplay exited during PCM write")
                            if select.select([], [fd], [], 0)[1]:
                                try:
                                    count = os.write(fd, pcm[offset:])
                                    offset += count
                                    if count and self._first_packet_at is not None:
                                        elapsed = time.monotonic() - self._first_packet_at
                                        self._timing["first_packet_to_write_ms"] = elapsed * 1000.0
                                        self._timing_max("max_first_packet_to_write_ms", elapsed)
                                        self._first_packet_at = None
                                    if count and not self.first_write_monotonic:
                                        self.first_write_monotonic = time.monotonic()
                                    self._play_until = max(time.monotonic(), self._play_until) + count / (rate * 2)
                                except BlockingIOError:
                                    pass
                    if offset < len(pcm):
                        self._stop.wait(.01)
                if close_pcm:
                    self._drain_playback(process, generation, stream_pcm)
                with self._lock:
                    if generation == self._generation:
                        self._inflight = False
        except Exception as error:
            self._fail(error)

    def finish_reply(self):
        with self._lock:
            self._end_queue_empty(time.monotonic())
            self._packet_at = None
            self._reply_end = self._generation

    def playback_busy(self):
        with self._lock:
            return self._reply_end == self._generation or super().playback_busy()

    def _drain_playback(self, process, generation, stream_pcm=False):
        with self._playback_lock:
            with self._lock:
                if generation != self._generation or self._stop.is_set() or self._interrupting:
                    return
                deadline = max(time.monotonic(), self._play_until) + 3
            process.stdin.close()
        while process.poll() is None:
            if time.monotonic() > deadline:
                raise AudioError("PCM playback drain timed out")
            self._stop.wait(.01)
            with self._lock:
                if generation != self._generation or self._stop.is_set() or self._interrupting:
                    return
        with self._playback_lock:
            with self._lock:
                if generation != self._generation or self._stop.is_set() or self._interrupting:
                    return
                if process.returncode != 0:
                    raise AudioError("PCM playback failed")
            self._terminate(process)
            self._playback = None
            with self._lock:
                self._play_until = 0.0
                if stream_pcm:
                    self._stream_open = self._stream_ending = self._stream_started = False
                    self._stream_rate = self._stream_bytes = 0
                    self._stream_deadline = 0.0

    def stop_capture(self):
        self._capture_stop.set()
        with self._lock:
            self._capture_callback = None
            process, thread = self._capture, self._capture_thread
        self._terminate(process)
        self._join(thread)
        with self._lock:
            if self._capture is process:
                self._capture = None
            if self._capture_thread is thread:
                self._capture_thread = None

    def stop(self):
        # Serialize the stop flag with the final write check, then reap playback
        # before waiting for a capture callback that may take time to return.
        with self._lock:
            self._stop.set()
            self._capture_stop.set()
            self._capture_callback = None
        errors = []
        for operation in (self.interrupt, self.stop_capture, lambda: self._join(self._output_thread)):
            try:
                operation()
            except Exception as error:
                errors.append(error)
                self.cleanup_uncertain = True
        if self._output_thread is not None and not self._output_thread.is_alive():
            self._output_thread = None
        with self._playback_lock:
            try:
                self._release_speaker()
            except Exception as error:
                errors.append(error)
        with self._lock:
            if not errors and self._aec:
                self._aec.close()
                self._aec = None
            if not errors and self._codec:
                self._codec.close()
                self._codec = None
        if errors:
            raise AudioError("audio worker or route cleanup is uncertain") from errors[0]
        return not self.cleanup_uncertain

    close = stop

    def snapshot(self):
        with self._lock:
            timing = dict(self._timing)
            if self._queue_empty_at is not None:
                timing["max_queue_empty_ms"] = max(timing["max_queue_empty_ms"],
                    (time.monotonic() - self._queue_empty_at) * 1000.0)
        return {"playback_timing": timing, "available": not self.cleanup_uncertain, "cleanup_uncertain": self.cleanup_uncertain,
                "capture_active": not self._capture_stop.is_set(), "speaker_owned": self._speaker_owned,
                "error": self.last_error, "volume": self.volume.snapshot(),
                "capture_device": self.config["capture_device"], "playback_device": self.config["playback_device"],
                "aec_verified": False, "wake_word_verified": False}
