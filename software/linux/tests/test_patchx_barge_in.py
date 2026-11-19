"""Near-end speech must pass both energy and VAD before interrupting."""
import struct
import unittest

from apps.patchx.handsfree import BargeInGate


def frame(amplitude):
    return struct.pack("<960h", *([amplitude] * 960))


class BargeInTests(unittest.TestCase):
    def test_low_energy_vad_echo_does_not_accumulate(self):
        gate = BargeInGate(min_rms=600)
        for _ in range(100):
            self.assertFalse(gate.accept(frame(599), True))
        self.assertEqual(gate.voice_frames, 0)

    def test_loud_non_speech_is_rejected(self):
        gate = BargeInGate(min_rms=600)
        for _ in range(20):
            self.assertFalse(gate.accept(frame(2000), False))

    def test_fifth_qualified_frame_interrupts_once(self):
        gate = BargeInGate()
        for _ in range(4):
            self.assertFalse(gate.accept(frame(1000), True))
        self.assertTrue(gate.accept(frame(1000), True))
        self.assertFalse(gate.accept(frame(1000), True))

    def test_short_gap_keeps_count_and_twelve_silent_frames_reset(self):
        gate = BargeInGate()
        for _ in range(4):
            self.assertFalse(gate.accept(frame(1000), True))
        for _ in range(11):
            self.assertFalse(gate.accept(frame(0), False))
        self.assertTrue(gate.accept(frame(1000), True))
        gate.reset()
        for _ in range(4):
            gate.accept(frame(1000), True)
        for _ in range(12):
            gate.accept(frame(0), False)
        self.assertFalse(gate.accept(frame(1000), True))
        self.assertEqual(gate.voice_frames, 1)

    def test_invalid_frame_does_not_change_state(self):
        gate = BargeInGate()
        with self.assertRaises(ValueError):
            gate.accept(b"truncated", True)
        self.assertEqual(gate.frames, 0)
