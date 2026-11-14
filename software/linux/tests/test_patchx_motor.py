"""PatchX MCU protocol and serial owner exercised through a Linux PTY."""
import os
import json
import pty
import select
import struct
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.patchx.motor import MotorController, FrameReader, encode_frame
from apps.patchx.__main__ import create_runtime


class MotorTest(unittest.TestCase):
    def setUp(self):
        self.master, self.slave = pty.openpty()
        self.motor = MotorController(os.ttyname(self.slave))
        self.frames = []
        self.drop_action_ack = False
        self.stop_event = threading.Event()
        self.peer = threading.Thread(target=self.board, daemon=True)
        self.peer.start()
        self.runtime = create_runtime({}, motor_controller=self.motor, http_address=("127.0.0.1", 0))
        self.addCleanup(self.runtime.close)
        self.runtime.start()
        self.wait_for(lambda: self.motor.snapshot()["connected"])

    def tearDown(self):
        self.runtime.close()
        self.stop_event.set()
        self.peer.join(timeout=1)
        os.close(self.master)
        os.close(self.slave)

    def wait_for(self, condition):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(.002)
        self.fail("condition did not arrive")

    def board(self):
        reader = FrameReader()
        watermark = b"\x00\x64"
        while not self.stop_event.is_set():
            if not select.select([self.master], [], [], .02)[0]:
                continue
            for cmd, payload in reader.feed(os.read(self.master, 512)):
                self.frames.append((time.monotonic(), cmd, payload))
                if cmd == 0xE1:
                    continue
                if cmd == 0x6B:
                    body = payload + b"\x00\x01" + watermark
                elif cmd == 0x64:
                    body = payload + b"\x00\x00"
                elif cmd == 0x62:
                    body = payload[:2] + b"\x00" + payload[2:3] + bytes(15)
                elif cmd == 0x6A:
                    body = payload[:2] + b"\x00" + payload[2:3] + struct.pack(">II", 100000, 4000)
                else:
                    watermark = payload[:2]
                    if cmd == 0x66 and self.drop_action_ack:
                        continue
                    body = payload[:2] + b"\x00"
                os.write(self.master, encode_frame(cmd, body))

    def test_latest_target_limited_and_stop_disarms(self):
        self.assertFalse(any(cmd in (0x6F, 0x67, 0x68) for _, cmd, _ in self.frames))
        self.assertEqual(self.motor.begin(3).result(1)["result"], 0)
        self.assertIn((0x67, b"\x00\x65\x03"), [(cmd, body) for _, cmd, body in self.frames])
        for point in range(30):
            self.assertTrue(self.motor.target([point] * 6))
            time.sleep(.002)
        self.wait_for(lambda: sum(cmd == 0x68 for _, cmd, _ in self.frames) >= 3)
        stop = self.motor.stop()
        self.assertFalse(stop.cancel())
        stop.result(1)
        sent = [(stamp, struct.unpack_from(">i", body, 2)[0]) for stamp, cmd, body in self.frames if cmd == 0x68]
        self.assertLess(len(sent), 8)
        self.assertTrue(all(b[0] - a[0] >= .017 for a, b in zip(sent, sent[1:])))
        self.assertTrue(all(b[1] > a[1] for a, b in zip(sent, sent[1:])))
        self.assertFalse(self.motor.target([0] * 6))

    def test_idle_faults_are_acked_and_deduplicated(self):
        body = struct.pack(">HBHIB", 8, 1, 4, 30, 1)
        for _ in range(2):
            os.write(self.master, encode_frame(0xE0, body))
        self.wait_for(lambda: sum(cmd == 0xE1 for _, cmd, _ in self.frames) == 2)
        state = self.motor.snapshot()
        self.assertEqual([(f["axis"], f["code"], f["active"]) for f in state["faults"]], [(1, 4, True)])
        url = f"http://127.0.0.1:{self.runtime.server.server_port}/api/status"
        with urllib.request.urlopen(url, timeout=1) as response:
            self.assertEqual(json.load(response)["patchx"]["motor"]["faults"], state["faults"])
        os.write(self.master, encode_frame(0xE0, struct.pack(">HBHIB", 9, 1, 4, 100, 0)))
        self.wait_for(lambda: self.motor.snapshot()["faults"][0]["active"] is False)

    def test_pd_is_read_back_and_builtin_action_uses_firmware_id(self):
        self.assertEqual(self.motor.set_pd(1, .1, .004).result(1)["result"], 0)
        self.assertEqual(self.motor.get_pd(1).result(1)["kp"], .1)
        self.assertEqual(self.motor.action(4).result(1)["result"], 0)
        payloads = {cmd: data for _, cmd, data in self.frames}
        self.assertEqual(payloads[0x69][2:], struct.pack(">BII", 1, 100000, 4000))
        self.assertEqual(payloads[0x66][2:], b"\x00\x04")

    def test_lost_action_ack_still_sends_stop(self):
        self.drop_action_ack = True
        with self.assertRaises(TimeoutError):
            self.motor.action(4).result(1)
        self.wait_for(lambda: any(cmd == 0x63 for _, cmd, _ in self.frames))
        self.wait_for(lambda: not self.motor.snapshot()["connected"])
        self.assertFalse(self.motor.snapshot()["stop_uncertain"])


if __name__ == "__main__":
    unittest.main()
