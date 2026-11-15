"""Exercise the device-owned cloud conversation lifecycle with fake audio."""
import unittest

from apps.companion.audio import PcmAudio
from apps.patchx.business import PatchXBusiness
from apps.patchx.config import validate_product
from apps.patchx.product import PatchXCompanion
from test_patchx_support import FakeAudio, FakeCodec, FakeTransport, wait_for


class Audio(FakeAudio):
    def start(self, callback):
        self.frame = callback
        self.recording = True

    def pause_capture(self):
        self.recording = False

    def playback_busy(self):
        return getattr(self, "playing", False)

    def play(self, packet):
        self.playing = True

    def play_pcm(self, audio):
        self.prompt = audio
        self.playing = True

    def interrupt(self):
        self.playing = False


class Detector:
    def reset_wake(self):
        pass

    def reset_speech(self):
        pass

    def wake(self, frame):
        return frame == b"wake"

    def speech(self, frame):
        return frame == b"speech"


class HandsFreeTests(unittest.TestCase):
    def test_wake_prompt_finishes_before_microphone_uplink(self):
        voice = validate_product({
            "voice": {"mode": "live", "device_id": "fixture", "client_id": "fixture",
                      "backend_url": "wss://example.invalid", "listen_mode": "realtime"},
            "patchx": {"client_ip": "192.0.2.1"},
        })["voice"]
        prompt = PcmAudio(bytes(320), 16000)
        core = PatchXCompanion(voice, PatchXBusiness(), handsfree=Detector(), wake_prompt=prompt,
                               audio_factory=Audio, codec_factory=FakeCodec,
                               transport_factory=FakeTransport)
        self.addCleanup(core.close)
        core.start()
        wait_for(lambda: core.snapshot()["wake_waiting"])
        core.standby_audio.frame(b"wake")
        wait_for(lambda: core.wake_prompt_pending)
        self.assertEqual(core.audio.prompt, prompt)
        self.assertFalse(core.audio.recording)
        self.assertEqual([m["type"] for m in core.transport.sent], ["hello"])
        core.audio.playing = False
        wait_for(lambda: core.audio.recording)
        self.assertEqual(core.transport.sent[-1]["state"], "start")

    def test_wake_followup_barge_in_and_mute(self):
        voice = validate_product({
            "voice": {"mode": "live", "device_id": "fixture", "client_id": "fixture",
                      "backend_url": "wss://example.invalid", "listen_mode": "realtime"},
            "patchx": {"client_ip": "192.0.2.1"},
        })["voice"]
        core = PatchXCompanion(voice, PatchXBusiness(), handsfree=Detector(), wake_barge_in=True,
                               audio_factory=Audio, codec_factory=FakeCodec,
                               transport_factory=FakeTransport)
        self.addCleanup(core.close)
        core.start()
        wait_for(lambda: core.snapshot()["wake_waiting"])
        standby = core.standby_audio
        standby.frame(b"not a keyword")
        self.assertIsNone(core.transport)
        standby.frame(b"wake")
        wait_for(lambda: core.snapshot()["state"] == "listening")
        transport, audio = core.transport, core.audio
        self.assertTrue(standby.closed)
        self.assertEqual(transport.sent[-1]["mode"], "realtime")
        transport.message({"type": "tts", "state": "start"})
        transport.message(b"reply")
        wait_for(audio.playback_busy)
        self.assertTrue(audio.recording)
        transport.message({"type": "tts", "state": "stop"})
        audio.playing = False
        wait_for(lambda: sum(isinstance(m, dict) and m.get("type") == "listen"
                             for m in transport.sent) == 2)
        self.assertIs(core.transport, transport)
        transport.message({"type": "tts", "state": "start"})
        transport.message(b"long reply")
        wait_for(audio.playback_busy)
        audio.frame(b"echo")
        for _ in range(3):
            audio.frame(b"speech")
        wait_for(lambda: transport.sent[-1] == b"opus")
        self.assertIn({"type": "interrupt", "session_id": "fixture"}, transport.sent)
        self.assertFalse(audio.playback_busy())
        self.assertEqual(core.snapshot()["state"], "listening")
        core.action("mute")
        self.assertFalse(core.wake_armed)
        self.assertFalse(core.snapshot()["wake_waiting"])
