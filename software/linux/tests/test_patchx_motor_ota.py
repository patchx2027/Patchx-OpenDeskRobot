import struct
import unittest
from unittest.mock import patch

from apps.patchx import motor_ota


class Wire:
    def __init__(self, final_ack=True, early_ack=True):
        self.final_ack = final_ack
        self.early_ack = early_ack
        self.frames = []
        self.sent = []
        self.seq = 0
        self.restarted = False

    def close(self):
        pass

    def write(self, cmd, data):
        self.sent.append((cmd, data))
        if cmd == 0xCC:
            self.frames.append((cmd, b"TO_G-test"))
        elif cmd == 0x6B:
            self.frames.append((cmd, data + b"\x04\x01" + struct.pack(">H", self.seq)))
        elif cmd == 0x63:
            self.seq = struct.unpack(">H", data)[0]
            self.frames.append((cmd, data + b"\0"))
        elif cmd == 0xDE:
            self.frames.append((cmd, b""))
        elif cmd == 0xDF:
            index, size = struct.unpack_from(">HH", data)
            if index == 1 and self.early_ack:
                self.frames.append((0xE0, struct.pack(">HBHIB", 8, 1, 3, 100, 1)))
                self.frames.append((cmd, b""))
            if index == 2:
                self.seq = 0
                self.restarted = True
                if self.final_ack:
                    self.frames.append((cmd, b""))
        elif cmd == 0x64:
            self.frames.append((cmd, data + b"\0\0"))

    def receive(self, deadline):
        return self.frames.pop(0) if self.frames else None


class MotorOtaTest(unittest.TestCase):
    image = struct.pack("<II", 0x20010000, 0x08004009) + b"x" * 250

    def run_upgrade(self, wire):
        with patch.object(motor_ota, "_Serial", return_value=wire):
            return motor_ota.upgrade("/dev/test", self.image)

    def test_upload_restarts_and_handles_faults_during_transfer(self):
        wire = Wire()
        result = self.run_upgrade(wire)
        self.assertTrue(result["transfer_acknowledged"])
        self.assertTrue(result["app_restarted"])
        self.assertEqual(result["image_confirmation"], "requires_readback")
        self.assertEqual(result["acked_packets"], 2)
        blocks = [data for cmd, data in wire.sent if cmd == 0xDF]
        self.assertEqual([struct.unpack_from(">HH", data) for data in blocks], [(1, 256), (2, 2)])
        self.assertEqual(b"".join(data[4:] for data in blocks), self.image)
        self.assertIn((0xE1, b"\0\x08"), wire.sent)

    def test_missing_last_ack_does_not_retransmit_or_claim_transfer_complete(self):
        wire = Wire(final_ack=False)
        result = self.run_upgrade(wire)
        self.assertFalse(result["transfer_acknowledged"])
        self.assertTrue(result["app_restarted"])
        self.assertEqual(result["acked_packets"], 1)
        self.assertEqual(sum(cmd == 0xDF for cmd, _ in wire.sent), 2)

    def test_missing_middle_ack_does_not_send_next_block(self):
        wire = Wire(early_ack=False)
        with self.assertRaises(TimeoutError):
            self.run_upgrade(wire)
        self.assertFalse(wire.restarted)
        self.assertEqual(sum(cmd == 0xDF for cmd, _ in wire.sent), 1)

    def test_invalid_vectors_do_not_open_uart(self):
        with patch.object(motor_ota, "_Serial") as serial:
            with self.assertRaises(ValueError):
                motor_ota.upgrade("/dev/test", b"\0" * 128)
        serial.assert_not_called()
