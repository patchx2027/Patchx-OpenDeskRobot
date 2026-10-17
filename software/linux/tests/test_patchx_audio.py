"""Real PCM pipe/Opus and libasound routing tests; no hardware access."""
import ctypes
import ctypes.util
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apps.companion import audio as common
from apps.patchx.audio import PatchXAudioIO, RK809SpeakerRoute, SoftwareVolume


def eventually(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("bounded audio condition did not occur")


class RoutePort:
    def __init__(self):
        self.events = []
        self.adapter = None
        self.release_ok = True
        self.acquire_ok = True
        self.before_spawn = None
        self.after_exit = None

    def acquire(self):
        self.events.append("SPK")
        self.before_spawn = self.adapter._playback is None
        return self.acquire_ok

    def release(self):
        process = self.adapter._playback
        self.after_exit = process is None or (process.poll() is not None and process.stdin.closed)
        self.events.append("OFF")
        return self.release_ok


class PipeAudio(PatchXAudioIO):
    def __init__(self, directory, route, raw=15, on_error=None, child_delay=0):
        self.directory = Path(directory)
        self.output = self.directory / "actual.pcm"
        self.capture_input = self.directory / "capture.pcm"
        self.child_delay = child_delay
        self.spawn_failure = False
        super().__init__({}, on_error, speaker_route=route, volume=SoftwareVolume(raw))
        route.adapter = self

    def _command(self, capture, sample_rate=None):
        if self.spawn_failure:
            return ["/missing-patchx-audio-fixture"]
        if capture:
            code = "import sys,time;sys.stdout.buffer.write(open(sys.argv[1],'rb').read());sys.stdout.buffer.flush();time.sleep(30)"
            return [sys.executable, "-c", code, str(self.capture_input)]
        code = ("import sys,time;data=sys.stdin.buffer.read();"
                "open(sys.argv[1],'wb').write(data);time.sleep(float(sys.argv[2]))")
        return [sys.executable, "-c", code, str(self.output), str(self.child_delay)]


class AudioLeafTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="patchx-audio-")
        self.route = RoutePort()
        self.errors = []
        self.adapter = PipeAudio(self.temporary.name, self.route, on_error=self.errors.append)
        self.probe = mock.patch.object(common, "prerequisites", return_value={"aplay": "fixture", "libopus": "real"})
        self.probe.start()

    def tearDown(self):
        try:
            self.adapter.stop()
        except common.AudioError:
            pass
        self.probe.stop()
        self.temporary.cleanup()

    def play_local(self, samples):
        self.adapter.play_pcm(common.PcmAudio(struct.pack("<" + str(len(samples)) + "h", *samples), 16000))
        eventually(lambda: self.adapter.output.exists() and not self.adapter.playback_busy())
        return struct.unpack("<" + str(len(samples)) + "h", self.adapter.output.read_bytes())

    def test_construction_and_volume_changes_open_nothing(self):
        with mock.patch("apps.patchx.audio.subprocess.Popen", side_effect=AssertionError("opened")):
            self.adapter.volume.set_raw(0)
            self.adapter.volume.adjust(100)
            self.assertEqual(self.adapter.volume.snapshot()["raw"], 15)
        self.assertEqual(self.route.events, [])

    def test_zero_unity_and_intermediate_gain_reach_real_child_pipe(self):
        original = (-32768, -15000, -1, 0, 1, 15000, 32767)
        for raw in (0, 15, 7):
            with self.subTest(raw=raw):
                if self.adapter.output.exists():
                    self.adapter.output.unlink()
                self.adapter.volume.set_raw(raw)
                actual = self.play_local(original)
                expected = tuple((1 if value >= 0 else -1) * ((abs(value) * raw + 7) // 15) for value in original)
                self.assertEqual(actual, expected)
        self.assertEqual(self.route.events, ["SPK", "OFF"] * 3)
        self.assertTrue(self.route.before_spawn)
        self.assertTrue(self.route.after_exit)

    def test_decoded_real_opus_pcm_is_attenuated(self):
        codec = common.OpusCodec()
        pcm = struct.pack("<960h", *([12000, -12000] * 480))
        packet = codec.encode(pcm)
        codec.close()
        for raw in (0, 7, 15):
            with self.subTest(raw=raw):
                self.adapter.stop()
                if self.adapter.output.exists():
                    self.adapter.output.unlink()
                independent_decoder = common.OpusCodec()
                expected = SoftwareVolume(raw).apply(independent_decoder.decode(packet))
                independent_decoder.close()
                self.adapter.volume.set_raw(raw)
                self.adapter.play(packet)
                eventually(lambda: self.adapter.first_write_monotonic > 0)
                process = self.adapter._playback
                # Actual EOF drains the fixture; interrupt then returns ownership.
                process.stdin.close()
                eventually(lambda: process.poll() is not None)
                self.adapter.interrupt()
                self.assertEqual(self.adapter.output.read_bytes(), expected)
                if raw > 0:
                    self.assertNotEqual(expected, bytes(1920))
        self.assertEqual(self.route.events, ["SPK", "OFF"] * 3)

    def test_normal_local_drain_restores_only_after_child_exit(self):
        self.adapter.child_delay = 0.15
        self.adapter.play_pcm(common.PcmAudio(bytes(1920), 16000))
        eventually(lambda: self.adapter.output.exists())
        self.assertEqual(self.route.events, ["SPK"])
        eventually(lambda: not self.adapter.playback_busy())
        self.assertEqual(self.route.events, ["SPK", "OFF"])
        self.assertTrue(self.route.after_exit)

    def test_interrupt_reaps_actual_child_before_restoring_route(self):
        codec = common.OpusCodec()
        packet = codec.encode(bytes(1920))
        codec.close()
        self.adapter.play(packet)
        eventually(lambda: self.adapter.first_write_monotonic > 0)
        process = self.adapter._playback
        self.adapter.interrupt()
        self.assertIsNotNone(process.poll())
        self.assertTrue(process.stdin.closed)
        self.assertEqual(self.route.events, ["SPK", "OFF"])
        self.assertTrue(self.route.after_exit)
        self.adapter.stop()
        self.adapter.stop()
        self.assertEqual(self.route.events, ["SPK", "OFF"])

    def test_spawn_failure_restores_acquired_route(self):
        self.adapter.spawn_failure = True
        self.adapter.play_pcm(common.PcmAudio(bytes(1920), 16000))
        eventually(lambda: self.errors)
        self.assertEqual(self.route.events, ["SPK", "OFF"])
        self.assertFalse(self.adapter.cleanup_uncertain)

    def test_refused_acquire_never_spawns_pcm(self):
        self.route.acquire_ok = False
        popen = subprocess.Popen
        def forbid_pcm(command, **kwargs):
            if command[0] == sys.executable:
                raise AssertionError("PCM child opened")
            return popen(command, **kwargs)
        with mock.patch("apps.patchx.audio.subprocess.Popen", side_effect=forbid_pcm):
            self.adapter.play_pcm(common.PcmAudio(bytes(1920), 16000))
            eventually(lambda: self.errors)
        self.assertEqual(self.route.events, ["SPK", "OFF"])

    def test_restore_failure_latches_and_refuses_restart(self):
        self.route.release_ok = False
        self.adapter.play_pcm(common.PcmAudio(bytes(1920), 16000))
        eventually(lambda: self.adapter.cleanup_uncertain)
        self.adapter.stop()
        self.assertFalse(self.adapter.snapshot()["available"])
        with self.assertRaises(common.AudioError):
            self.adapter.play_pcm(common.PcmAudio(bytes(1920), 16000))
        self.assertEqual(self.route.events, ["SPK", "OFF"])

    def test_unreaped_pcm_keeps_speaker_owned_and_latches_uncertainty(self):
        ready = Path(self.temporary.name) / "child-ready"
        original_command = self.adapter._command
        def blocked(capture, sample_rate=None):
            if capture:
                return original_command(capture, sample_rate)
            return [sys.executable, "-c",
                    "import signal,sys,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                    "open(sys.argv[1],'w').write('ready');time.sleep(30)", str(ready)]
        self.adapter._command = blocked
        codec = common.OpusCodec()
        packet = codec.encode(bytes(1920))
        codec.close()
        self.adapter.play(packet)
        eventually(lambda: ready.exists())
        process = self.adapter._playback
        try:
            with mock.patch.object(process, "kill", return_value=None):
                with self.assertRaises(subprocess.TimeoutExpired):
                    self.adapter.interrupt()
            self.assertTrue(self.adapter.cleanup_uncertain)
            self.assertEqual(self.route.events, ["SPK"])
            self.assertTrue(self.adapter._speaker_owned)
            self.assertIs(self.adapter._playback, process)
            self.assertIsNone(process.poll())
        finally:
            process.kill()
            process.wait(timeout=2)
            self.adapter.stop()
        self.assertEqual(self.route.events, ["SPK", "OFF"])
        self.assertTrue(self.route.after_exit)

    def test_capture_preserves_16k_mono_pcm_and_does_not_enable_speaker(self):
        original = struct.pack("<960h", *range(-480, 480))
        self.adapter.capture_input.write_bytes(original)
        received = []
        with mock.patch.object(common.shutil, "which", return_value="fixture"):
            self.adapter.start(received.append)
            eventually(lambda: received)
            self.adapter.stop_capture()
        self.assertEqual(received, [original])
        self.assertEqual(self.route.events, [])

    def test_blocked_capture_callback_close_is_bounded_and_refuses_restart(self):
        self.adapter.capture_input.write_bytes(bytes(1920))
        entered, leave = threading.Event(), threading.Event()
        def blocked(pcm):
            entered.set()
            leave.wait(10)
        with mock.patch.object(common.shutil, "which", return_value="fixture"):
            self.adapter.start(blocked)
            self.assertTrue(entered.wait(2))
            started = time.monotonic()
            try:
                with self.assertRaises(common.AudioError):
                    self.adapter.stop()
                self.assertLess(time.monotonic() - started, 3.5)
                self.assertTrue(self.adapter.cleanup_uncertain)
                self.assertTrue(self.adapter._capture_thread.is_alive())
                with self.assertRaises(common.AudioError):
                    self.adapter.play_pcm(common.PcmAudio(bytes(1920), 16000))
            finally:
                leave.set()
                self.adapter.stop()
        self.assertEqual(self.route.events, [])

    def test_logical_volume_legacy_and_absolute_semantics(self):
        volume = SoftwareVolume(8)
        self.assertEqual(volume.adjust(-30)["raw"], 3)
        self.assertEqual(volume.adjust(30)["raw"], 8)
        self.assertEqual(volume.set_raw(7)["raw"], 7)
        self.assertFalse(volume.set_raw(16)["ok"])
        self.assertEqual(volume.snapshot()["raw"], 7)
        self.assertFalse(volume.snapshot()["hardware_gain"])
        for value in (True, "5", 2**32):
            with self.assertRaises(ValueError):
                volume.set_raw(value)


class MixerFixture:
    def __init__(self):
        self.values = {name: 0 for name in RK809SpeakerRoute.CONTROLS}
        self.calls = []
        self.fail_on = None
        self.bad_label = False

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        if command[3] == "cset":
            name = command[4][5:]
            if command[-1] == self.fail_on:
                return subprocess.CompletedProcess(command, 1, "", "fixture failure")
            self.values[name] = RK809SpeakerRoute.CONTROLS[name].index(command[5])
        name = command[4][5:]
        labels = RK809SpeakerRoute.CONTROLS[name]
        text = ("numid=3,iface=MIXER,name='" + name + "'\n"
                "  ; type=ENUMERATED,access=rw------,values=1,items=" + str(len(labels)) + "\n")
        for index, label in enumerate(labels):
            text += "  ; Item #" + str(index) + " '" + ("wrong" if self.bad_label else label) + "'\n"
        text += "  : values=" + str(self.values[name]) + "\n"
        return subprocess.CompletedProcess(command, 0, text, "")


class SpeakerRouteTests(unittest.TestCase):
    def test_all_off_spk_and_restoration_verify_all_controls(self):
        fixture = MixerFixture()
        route = RK809SpeakerRoute(runner=fixture)
        self.assertEqual(fixture.calls, [])
        self.assertTrue(route.acquire())
        self.assertEqual(fixture.values["Playback Path"], 2)
        self.assertTrue(route.release())
        self.assertTrue(route.release())
        self.assertEqual(set(fixture.values.values()), {0})
        self.assertEqual([call[-1] for call in fixture.calls if call[3] == "cset"], ["SPK", "OFF"])
        self.assertFalse(route.snapshot()["cleanup_uncertain"])

    def test_bad_control_or_nonoff_baseline_writes_nothing(self):
        for bad_label, current in ((True, 0), (False, 1)):
            fixture = MixerFixture()
            fixture.bad_label = bad_label
            fixture.values["Capture MIC Path"] = current
            route = RK809SpeakerRoute(runner=fixture)
            with self.assertRaises(common.AudioError):
                route.acquire()
            self.assertFalse(any(call[3] == "cset" for call in fixture.calls))

    def test_failed_enable_latches_even_if_cached_off_verifies(self):
        fixture = MixerFixture()
        fixture.fail_on = "SPK"
        route = RK809SpeakerRoute(runner=fixture)
        with self.assertRaises(common.AudioError):
            route.acquire()
        self.assertTrue(route.cleanup_uncertain)
        self.assertEqual(set(fixture.values.values()), {0})
        self.assertFalse(route.release())
        with self.assertRaises(common.AudioError):
            route.acquire()

    def test_failed_off_latches_and_does_not_retry_write(self):
        fixture = MixerFixture()
        route = RK809SpeakerRoute(runner=fixture)
        route.acquire()
        fixture.fail_on = "OFF"
        self.assertFalse(route.release())
        self.assertFalse(route.release())
        self.assertEqual(len([call for call in fixture.calls if call[3] == "cset"]), 2)


class AlsaRoutingTests(unittest.TestCase):
    @staticmethod
    def _library():
        lib = ctypes.CDLL(ctypes.util.find_library("asound"))
        ptr = ctypes.c_void_p
        lib.snd_config_top.argtypes = [ctypes.POINTER(ptr)]
        lib.snd_input_stdio_open.argtypes = [ctypes.POINTER(ptr), ctypes.c_char_p, ctypes.c_char_p]
        lib.snd_config_load.argtypes = [ptr, ptr]
        lib.snd_pcm_open_lconf.argtypes = [ctypes.POINTER(ptr), ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ptr]
        lib.snd_pcm_set_params.argtypes = [ptr, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
        lib.snd_pcm_readi.argtypes = [ptr, ptr, ctypes.c_ulong]
        lib.snd_pcm_readi.restype = ctypes.c_long
        lib.snd_pcm_writei.argtypes = [ptr, ptr, ctypes.c_ulong]
        lib.snd_pcm_writei.restype = ctypes.c_long
        for name in ("snd_pcm_close", "snd_pcm_drain", "snd_input_close", "snd_config_delete", "snd_output_close"):
            getattr(lib, name).argtypes = [ptr]
        lib.snd_output_buffer_open.argtypes = [ctypes.POINTER(ptr)]
        lib.snd_output_buffer_string.argtypes = [ptr, ctypes.POINTER(ctypes.c_char_p)]
        lib.snd_output_buffer_string.restype = ctypes.c_size_t
        lib.snd_pcm_dump.argtypes = [ptr, ptr]
        return lib

    def test_production_config_actual_eight_channel_capture_and_stereo_rates(self):
        # Only the hardware slave names are replaced. libasound parses and runs
        # the exact production plug/ttable configuration against real file PCM.
        lib = self._library()
        def checked(result):
            self.assertGreaterEqual(result, 0, "actual libasound error " + str(result))
            return result
        with tempfile.TemporaryDirectory(prefix="patchx-alsa-") as temporary:
            directory = Path(temporary)
            source = directory / "eight.pcm"
            output = directory / "stereo.pcm"
            source.write_bytes(b"".join(struct.pack("<8h", frame - 480, 222, 333, 444, 555, 666, 777, 888) for frame in range(960)))
            production = (ROOT / "deploy/patchx/alsa-rk3568.conf").read_text()
            self.assertEqual(production.count("hw:CARD=Dongle,DEV=0"), 1)
            self.assertEqual(production.count("hw:CARD=rockchiprk809co,DEV=0"), 1)
            text = production.replace("hw:CARD=Dongle,DEV=0", "fixture_usb").replace("hw:CARD=rockchiprk809co,DEV=0", "fixture_rk809")
            text += '\npcm.fixture_usb { type file slave.pcm "null" file "/dev/null" infile "' + str(source) + '" format "raw" }\n'
            text += '\npcm.fixture_rk809 { type file slave.pcm "null" file "' + str(output) + '" format "raw" }\n'
            configuration = directory / "alsa.conf"
            configuration.write_text(text)
            conf, inp = ctypes.c_void_p(), ctypes.c_void_p()
            checked(lib.snd_config_top(ctypes.byref(conf)))
            checked(lib.snd_input_stdio_open(ctypes.byref(inp), str(configuration).encode(), b"r"))
            try:
                checked(lib.snd_config_load(conf, inp))
            finally:
                lib.snd_input_close(inp)
            try:
                for capture, rate in ((True, 16000), (False, 16000), (False, 24000), (False, 48000)):
                    with self.subTest(capture=capture, negotiated_rate=rate):
                        pcm = ctypes.c_void_p()
                        checked(lib.snd_pcm_open_lconf(ctypes.byref(pcm), b"patchx_capture" if capture else b"patchx_playback", int(capture), 0, conf))
                        try:
                            checked(lib.snd_pcm_set_params(pcm, 2, 3, 1, rate, 1, 120000))
                            dump, buffer = ctypes.c_void_p(), ctypes.c_char_p()
                            checked(lib.snd_output_buffer_open(ctypes.byref(dump)))
                            try:
                                checked(lib.snd_pcm_dump(pcm, dump))
                                lib.snd_output_buffer_string(dump, ctypes.byref(buffer))
                                native = buffer.value.decode().split("Final file PCM", 1)[1]
                                self.assertIn("format       : S16_LE", native)
                                self.assertIn("channels     : " + str(8 if capture else 2), native)
                                self.assertIn("rate         : " + str(16000 if capture else 48000), native)
                            finally:
                                lib.snd_output_close(dump)
                            samples = (ctypes.c_int16 * 960)(*range(-480, 480))
                            if capture:
                                self.assertEqual(checked(lib.snd_pcm_readi(pcm, samples, 960)), 960)
                                self.assertEqual(list(samples), list(range(-480, 480)))
                            else:
                                self.assertEqual(checked(lib.snd_pcm_writei(pcm, samples, 960)), 960)
                                checked(lib.snd_pcm_drain(pcm))
                        finally:
                            checked(lib.snd_pcm_close(pcm))
                        if not capture:
                            actual = output.read_bytes()
                            self.assertEqual(len(actual), 960 * 48000 // rate * 4)
                            stereo = struct.unpack("<" + str(len(actual) // 2) + "h", actual)
                            self.assertEqual(stereo[::2], stereo[1::2])
                            if rate == 48000:
                                self.assertEqual(stereo[::2], tuple(range(-480, 480)))
                            output.unlink()
            finally:
                checked(lib.snd_config_delete(conf))


if __name__ == "__main__":
    unittest.main()
