"""RK3568 PatchX audio leaf: explicit routes and logical PCM attenuation.

Construction opens nothing. Capture selects USB channel zero; playback uses
ALSA conversion to the RK809 native format. CAE, wake-word and AEC equivalence
are not provided by this leaf. Speaker ownership is injected explicitly.
"""
import array
import queue
import re
import select
import subprocess
import sys
import threading
import time
import os

from apps.companion.audio import AudioError, AudioIO, PcmAudio, PcmStreamChunk
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

    def __init__(self, card_id="rockchiprk809co", runner=None):
        if card_id != "rockchiprk809co":
            raise ValueError("this route requires the verified RK809 card identity")
        self.card_id = card_id
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
                self._call("cset", "Playback Path", "SPK")
                after = self._all()
                if after != {"Playback Path": 2, "Capture MIC Path": 0, "Resume Path": 0}:
                    raise AudioError("RK809 SPK route did not verify")
                return True
            except Exception as error:
                self.cleanup_uncertain = True
                self.last_error = str(error)
                self.release()
                raise AudioError("RK809 SPK route could not be established") from error

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

    def __init__(self, config, on_error=None, *, speaker_route, volume=None):
        if not callable(getattr(speaker_route, "acquire", None)) or not callable(getattr(speaker_route, "release", None)):
            raise ValueError("an explicit speaker ownership port is required")
        board_config = dict(config)
        if board_config.get("aec_enabled"):
            raise ValueError("this basic USB route does not provide verified AEC")
        board_config["capture_device"] = CAPTURE_DEVICE
        board_config["playback_device"] = PLAYBACK_DEVICE
        super().__init__(board_config, on_error)
        self.speaker_route = speaker_route
        self.volume = volume if volume is not None else SoftwareVolume()
        if not isinstance(self.volume, SoftwareVolume):
            raise ValueError("expected the logical PCM volume port")
        self.cleanup_uncertain = False
        self._speaker_owned = False

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
            if self.speaker_route.release() is not True:
                raise AudioError("speaker route restoration did not verify")
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

    def _open_playback(self, rate):
        self._speaker_owned = True
        try:
            if self.speaker_route.acquire() is not True:
                raise AudioError("speaker route ownership was refused")
            self._playback = subprocess.Popen(self._command(False, rate), stdin=subprocess.PIPE,
                                              stdout=subprocess.DEVNULL, bufsize=0)
            os.set_blocking(self._playback.stdin.fileno(), False)
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
            self._stream_open = self._stream_ending = self._stream_started = False
            self._stream_rate = self._stream_bytes = 0
            self._stream_deadline = self._play_until = 0.0
            self._inflight = False
            while not self._queue.empty():
                self._queue.get_nowait()
            for process in (self._capture, self._playback):
                try:
                    self._terminate(process)
                except Exception:
                    self.cleanup_uncertain = True
            for name in ("_capture", "_playback"):
                process = getattr(self, name)
                if process is not None and process.poll() is not None:
                    streams = (process.stdin, process.stdout)
                    if all(stream is None or stream.closed for stream in streams):
                        setattr(self, name, None)
            try:
                self._release_speaker()
            except Exception:
                self.cleanup_uncertain = True
        if self.on_error:
            self.on_error(self.last_error)

    def _output(self):
        # Board-specific copy of the existing bounded PCM worker: ownership and
        # attenuation are placed at the real child/decoded-PCM boundaries.
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
                        raise AudioError("aplay exited unexpectedly")
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
                        pcm = self.volume.apply(pcm)
                        if self._playback is None:
                            self._open_playback(rate)
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
                        if time.monotonic() > write_deadline:
                            raise AudioError("PCM write timed out")
                        if process.poll() is not None:
                            raise AudioError("aplay exited during PCM write")
                        if select.select([], [fd], [], 0)[1]:
                            try:
                                count = os.write(fd, pcm[offset:])
                                offset += count
                                if count and not self.first_write_monotonic:
                                    self.first_write_monotonic = time.monotonic()
                                self._play_until = max(time.monotonic(), self._play_until) + count / (rate * 2)
                            except BlockingIOError:
                                pass
                    if offset < len(pcm):
                        self._stop.wait(0.01)
                if close_pcm:
                    with self._lock:
                        current = generation == self._generation and not self._stop.is_set()
                        if current:
                            process.stdin.close()
                    drain_deadline = max(time.monotonic(), self._play_until) + 3
                    while current and process.poll() is None:
                        if time.monotonic() > drain_deadline:
                            raise AudioError("Local PCM playback drain timed out")
                        self._stop.wait(0.01)
                        with self._lock:
                            current = generation == self._generation and not self._stop.is_set()
                    with self._lock:
                        if current:
                            if process.returncode != 0:
                                raise AudioError("Local PCM playback failed")
                            self._terminate(process)
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

    def stop(self):
        self._stop.set()
        errors = []
        for operation in (self.stop_capture, self.interrupt, lambda: self._join(self._output_thread)):
            try:
                operation()
            except Exception as error:
                errors.append(error)
                self.cleanup_uncertain = True
        if self._output_thread is not None and not self._output_thread.is_alive():
            self._output_thread = None
        with self._lock:
            try:
                self._release_speaker()
            except Exception as error:
                errors.append(error)
            if not errors and self._codec:
                self._codec.close()
                self._codec = None
        if errors:
            raise AudioError("audio worker or route cleanup is uncertain") from errors[0]
        return not self.cleanup_uncertain

    close = stop

    def snapshot(self):
        return {"available": not self.cleanup_uncertain, "cleanup_uncertain": self.cleanup_uncertain,
                "capture_active": not self._capture_stop.is_set(), "speaker_owned": self._speaker_owned,
                "error": self.last_error, "volume": self.volume.snapshot(),
                "capture_device": CAPTURE_DEVICE, "playback_device": PLAYBACK_DEVICE,
                "aec_verified": False, "wake_word_verified": False}
