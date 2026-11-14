"""Single-owner Linux UART for the GD32F503 motion protocol."""
import copy
import fcntl
import math
import os
import select
import struct
import termios
import threading
import time
from collections import deque
from concurrent.futures import Future

PERIOD = .020
STREAM_TIMEOUT = .100
REPLY_TIMEOUT = .060
FAULT_NAMES = {
    1: "编码器反馈异常", 2: "板端控制周期异常", 3: "运动或回零超时",
    4: "疑似堵转", 5: "方向限位", 0x101: "板端串口收包溢出",
    0x102: "板端流式目标超时", 0x201: "固件镜像异常",
    0x202: "固件读写异常", 0x203: "固件回滚", 0x204: "固件未确认",
}
RESULTS = ("accepted", "busy", "bad_parameter", "sequence_conflict", "not_ready",
           "faulted", "unsupported", "io_error")


def _crc(data):
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value


def encode_frame(command, payload):
    body = struct.pack(">HB", len(payload) + 1, command) + payload
    return b"\xff\xff" + body + struct.pack(">H", _crc(body)) + b"\x55\xaa"


class FrameReader:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        while True:
            offset = self.buffer.find(b"\xff\xff")
            if offset < 0:
                self.buffer[:] = b"\xff" if self.buffer.endswith(b"\xff") else b""
                return
            del self.buffer[:offset]
            if len(self.buffer) < 4:
                return
            size = int.from_bytes(self.buffer[2:4], "big") + 8
            if 9 <= size <= 273:
                if len(self.buffer) < size:
                    return
                frame = self.buffer[:size]
                if frame[-2:] == b"\x55\xaa" and _crc(frame[2:-4]) == int.from_bytes(frame[-4:-2], "big"):
                    del self.buffer[:size]
                    yield frame[4], bytes(frame[5:-4])
                    continue
            del self.buffer[0]


class _Serial:
    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | os.O_CLOEXEC)
        self.reader = FrameReader()
        self.frames = deque()
        self.last_read = 0
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.ioctl(self.fd, termios.TIOCEXCL)
            self.exclusive = True
            self.original = termios.tcgetattr(self.fd)
            attrs = termios.tcgetattr(self.fd)
            attrs[:6] = [0, 0, termios.CS8 | termios.CREAD | termios.CLOCAL, 0,
                         termios.B115200, termios.B115200]
            attrs[6][termios.VMIN], attrs[6][termios.VTIME] = 1, 0
            termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
            termios.tcflush(self.fd, termios.TCIFLUSH)
        except BaseException:
            self.close()
            raise

    def close(self):
        try:
            try:
                if hasattr(self, "original"):
                    termios.tcsetattr(self.fd, termios.TCSANOW, self.original)
            finally:
                if getattr(self, "exclusive", False):
                    fcntl.ioctl(self.fd, termios.TIOCNXCL)
        finally:
            os.close(self.fd)

    def write(self, command, body):
        packet = memoryview(encode_frame(command, body))
        deadline = time.monotonic() + REPLY_TIMEOUT
        while packet:
            if not select.select([], [self.fd], [], max(0, deadline - time.monotonic()))[1]:
                raise TimeoutError("serial write timed out")
            packet = packet[os.write(self.fd, packet):]

    def receive(self, deadline):
        while not self.frames:
            now = time.monotonic()
            if now >= deadline or not select.select([self.fd], [], [], deadline - now)[0]:
                return None
            data = os.read(self.fd, 512)
            if not data:
                raise OSError("serial disconnected")
            now = time.monotonic()
            if now - self.last_read > .030:
                self.reader.buffer.clear()
            self.last_read = now
            self.frames.extend(self.reader.feed(data))
        return self.frames.popleft()


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"expected integer in {low}..{high}")
    return value


def _scaled(value, limit, scale, low):
    if type(value) not in (int, float) or not low <= value <= limit or not math.isfinite(value):
        raise ValueError(f"expected finite value in {low}..{limit}")
    return round(value * scale)


class MotorController:
    """Public commands return Futures with the board result. Targets use a latest-value slot."""
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.thread = None
        self.job = self.stop_job = self.latest = None
        self.stream_until = self.next_send = 0.
        self.state = {"connected": False, "ready": False, "mode": "idle", "axes": [None] * 6,
                      "host_link": "starting", "link_fault": None, "stop_uncertain": False, "faults": []}
        self.faults = {}
        self.seq = self.query_seq = 0
        self.owned_motion = False
        self.stop_uncertain = False

    def start(self):
        if self.thread is not None or self.closed.is_set():
            raise RuntimeError("motor controller cannot be restarted")
        self.thread = threading.Thread(target=self._worker, name="patchx-motor", daemon=True)
        self.thread.start()

    def close(self):
        self.closed.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
            if self.thread.is_alive():
                raise RuntimeError("motor controller did not close")
            if self.stop_uncertain:
                raise RuntimeError("motor stop was not confirmed")

    def snapshot(self):
        with self.lock:
            state = copy.deepcopy(self.state)
        now = time.monotonic()
        for axis in state["axes"]:
            if axis is not None:
                axis["stale"] = state["mode"] == "streaming" or now - axis["sampled_monotonic"] > 2.5
        return state

    def begin(self, mask):
        return self._enqueue(0x67, bytes([_integer(mask, 1, 63)]))

    def target(self, degrees):
        if not isinstance(degrees, (tuple, list)) or len(degrees) != 6:
            raise ValueError("expected six joint angles in degrees")
        data = struct.pack(">6i", *(_scaled(value, 360, 100, -360) for value in degrees))
        with self.lock:
            now = time.monotonic()
            if self.closed.is_set() or self.state["mode"] != "streaming" or now >= self.stream_until:
                return False
            self.latest = (now, data)
            return True

    def set_pd(self, axis, kp, kd):
        return self._enqueue(0x69, struct.pack(">BII", _integer(axis, 0, 5),
                                              _scaled(kp, 1, 1000000, 0), _scaled(kd, 1, 1000000, 0)))

    def get_pd(self, axis):
        return self._enqueue(0x6A, bytes([_integer(axis, 0, 5)]))

    def action(self, action_id):
        return self._enqueue(0x66, struct.pack(">H", _integer(action_id, 1, 65535)))

    def clear_faults(self):
        return self._enqueue(0x65)

    def stop(self):
        return self._enqueue(0x63)

    def _enqueue(self, command, body=b""):
        with self.lock:
            if self.closed.is_set() or not self.state["connected"]:
                raise OSError("motor link unavailable")
            if command == 0x63:
                if self.stop_job is not None:
                    return self.stop_job[2]
                pending, self.job, self.latest = self.job, None, None
                self.stream_until = 0.
                future = Future()
                future.set_running_or_notify_cancel()
                self.stop_job = (command, body, future)
            else:
                if self.job is not None or self.stop_job is not None:
                    raise RuntimeError("motor command queue is busy")
                if self.state["mode"] == "streaming" and command not in (0x66, 0x6A):
                    raise RuntimeError("motor command requires leaving streaming mode")
                pending = None
                future = Future()
                self.job = (command, body, future)
        if pending is not None:
            pending[2].cancel()
        return future

    def _update_faults(self):
        self.state["faults"] = list(self.faults.values())

    @staticmethod
    def _decode_fault(body):
        if len(body) != 10:
            raise ValueError("invalid fault record length")
        seq, source, code, stamp, active = struct.unpack(">HBHIB", body)
        if not seq or not code or (source >= 6 and source not in (0xFD, 0xFE)) or active > 1:
            raise ValueError("invalid fault record")
        return {"source": "mcu", "axis": source if source < 6 else None, "source_id": source,
                "code": code, "event_seq": seq, "mcu_tick_ms": stamp, "active": bool(active),
                "message": FAULT_NAMES.get(code, f"未知板端故障 0x{code:04X}"),
                "blocks_motion": active == 1 and (source != 0xFD or code != 0x101)}

    def _receive(self, wire, deadline):
        frame = wire.receive(deadline)
        if frame is None or frame[0] != 0xE0:
            return frame
        try:
            event = self._decode_fault(frame[1])
        except ValueError:
            return None
        with self.lock:
            self.faults[event["source_id"]] = event
            self._update_faults()
            if event["blocks_motion"]:
                self.state["ready"] = False
                self.state["mode"] = "idle"
                self.latest = None
                self.stream_until = 0.
        wire.write(0xE1, frame[1][:2])
        return frame

    def _exchange(self, wire, command, data, prefix):
        wire.write(command, prefix + data)
        deadline = time.monotonic() + REPLY_TIMEOUT
        while time.monotonic() < deadline:
            frame = self._receive(wire, deadline)
            if frame is not None and frame[0] == command and frame[1].startswith(prefix):
                return frame[1]
        raise TimeoutError(f"no valid MCU reply for 0x{command:02X}")

    def _query(self, wire, command, data=b""):
        self.query_seq = self.query_seq % 65535 + 1
        return self._exchange(wire, command, data, struct.pack(">H", self.query_seq))

    @staticmethod
    def _result(body, length=3):
        if len(body) not in (3, length) or body[2] >= len(RESULTS) or (body[2] == 0 and len(body) != length):
            raise ValueError("invalid MCU response")
        return {"result": body[2], "status": RESULTS[body[2]]}

    def _readiness(self, wire):
        body = self._query(wire, 0x6B)
        if len(body) != 6 or body[3] != 1:
            raise ValueError("unsupported MCU motion protocol")
        self.seq = struct.unpack_from(">H", body, 4)[0]
        result = self._result(body[:3])
        with self.lock:
            self.state["ready"] = result["result"] == 0
        return result

    def _read_faults(self, wire):
        body = self._query(wire, 0x64)
        if len(body) < 4 or body[2] or body[3] > 8 or len(body) != 4 + 10 * body[3]:
            raise ValueError("invalid MCU fault snapshot")
        records = [self._decode_fault(body[i:i + 10]) for i in range(4, len(body), 10)]
        with self.lock:
            self.faults = {event["source_id"]: event for event in records}
            self._update_faults()

    def _read_axis(self, wire, axis):
        body = self._query(wire, 0x62, bytes([axis]))
        result = self._result(body, 19)
        if result["result"] or body[3] != axis or body[4] > 4 or body[6] > 1:
            raise ValueError("invalid MCU axis snapshot")
        position, target, velocity = struct.unpack_from(">iii", body, 7)
        with self.lock:
            self.state["axes"][axis] = {"state": body[4], "fault": body[5],
                                        "at_target": bool(body[6]), "position_deg": position / 100,
                                        "target_deg": target / 100, "velocity_deg_s": velocity / 100,
                                        "sampled_monotonic": time.monotonic()}

    def _command(self, wire, command, body):
        self.seq = self.seq % 65535 + 1
        prefix = struct.pack(">H", self.seq)
        try:
            reply = self._exchange(wire, command, body, prefix)
        except TimeoutError:
            if command != 0x63:
                raise
            reply = self._exchange(wire, command, body, prefix)
        return self._result(reply)

    def _stop_board(self, wire):
        result = self._command(wire, 0x63, b"")
        if result["result"] == 3:
            self._readiness(wire)
            result = self._command(wire, 0x63, b"")
        if result["result"] != 0:
            raise OSError("motor stop rejected by MCU")
        self.owned_motion = self.stop_uncertain = False
        return result

    def _execute(self, wire, command, body):
        if command == 0x6A:
            reply = self._query(wire, command, body)
            result = self._result(reply, 12)
            if result["result"] == 0:
                axis, kp, kd = struct.unpack_from(">BII", reply, 3)
                if axis != body[0] or kp > 1000000 or kd > 1000000:
                    raise ValueError("invalid MCU PD readback")
                result.update(axis=axis, kp=kp / 1000000, kd=kd / 1000000)
            return result
        sent_at = time.monotonic()
        owned_before = self.owned_motion
        if command in (0x66, 0x67):
            # A lost ACK can leave a one-shot board action running.
            self.owned_motion = True
        if command == 0x63:
            self.owned_motion = True
            result = self._stop_board(wire)
        else:
            result = self._command(wire, command, body)
        if result["result"] != 0:
            self.owned_motion = owned_before
        if result["result"] == 0:
            if command in (0x63, 0x66, 0x67):
                self.owned_motion = command != 0x63
                with self.lock:
                    self.latest = None
                    self.state["mode"] = {0x63: "idle", 0x66: "board_action", 0x67: "streaming"}[command]
                    self.next_send = sent_at
                    self.stream_until = sent_at + STREAM_TIMEOUT if command == 0x67 else 0.
            elif command == 0x65:
                self._read_faults(wire)
                self._readiness(wire)
        return result

    def _drive(self, wire):
        self._readiness(wire)
        if self.stop_uncertain:
            self._stop_board(wire)
        self._read_faults(wire)
        for axis in range(6):
            self._read_axis(wire, axis)
        with self.lock:
            self.state.update(connected=True, host_link="online", link_fault=None, stop_uncertain=False)
        poll = 0
        next_poll = time.monotonic() + .250
        while not self.closed.is_set():
            now = time.monotonic()
            with self.lock:
                job = self.stop_job or self.job
                if self.stop_job is not None:
                    self.stop_job = None
                else:
                    self.job = None
                if self.state["mode"] == "streaming" and now >= self.stream_until:
                    self.state["mode"] = "idle"
                    self.latest = None
                streaming = self.state["mode"] == "streaming"
                latest = None
                if job is None and streaming and self.latest is not None and now >= self.next_send:
                    latest, self.latest = self.latest, None
            if job is not None:
                command, body, future = job
                if command == 0x63 or future.set_running_or_notify_cancel():
                    try:
                        result = self._execute(wire, command, body)
                    except (OSError, ValueError) as error:
                        future.set_exception(error)
                        raise
                    future.set_result(result)
            elif latest is not None:
                arrived, body = latest
                if now - arrived < STREAM_TIMEOUT:
                    self.next_send = time.monotonic() + PERIOD
                    result = self._command(wire, 0x68, body)
                    with self.lock:
                        if result["result"] == 0 and self.state["mode"] == "streaming" and self.stream_until:
                            self.stream_until = self.next_send - PERIOD + STREAM_TIMEOUT
                        elif result["result"] not in (0, 1):
                            self.state["mode"] = "idle"
                            self.latest = None
            elif not streaming and now >= next_poll:
                if poll == 0:
                    self._readiness(wire)
                elif poll == 1:
                    self._read_faults(wire)
                else:
                    self._read_axis(wire, poll - 2)
                poll = (poll + 1) % 8
                next_poll = time.monotonic() + .250
            else:
                self._receive(wire, time.monotonic() + .003)

    def _offline(self, reason):
        with self.lock:
            jobs = self.job, self.stop_job
            self.job = self.stop_job = self.latest = None
            self.stream_until = 0.
            self.faults.clear()
            link_fault = None if reason == "closed" else {
                "source": "host_link", "code": reason, "active": True, "blocks_motion": True}
            self.state.update(connected=False, ready=False, mode="idle", axes=[None] * 6,
                              host_link=reason, link_fault=link_fault, faults=[], stop_uncertain=self.stop_uncertain)
        for job in jobs:
            if job is not None and (job[0] == 0x63 or job[2].set_running_or_notify_cancel()):
                job[2].set_exception(OSError("motor link unavailable"))

    def _worker(self):
        while not self.closed.is_set():
            wire = None
            reason = "closed"
            try:
                wire = _Serial(self.path)
                self._drive(wire)
            except (OSError, ValueError, termios.error) as error:
                reason = "reply_timeout" if isinstance(error, TimeoutError) else (
                    "invalid_protocol" if isinstance(error, ValueError) else "serial_error")
            finally:
                if wire is not None:
                    if self.owned_motion:
                        try:
                            self._stop_board(wire)
                        except (OSError, ValueError):
                            self.stop_uncertain = True
                    try:
                        wire.close()
                    except (OSError, termios.error):
                        reason = "serial_error"
                self._offline(reason)
            self.closed.wait(.5)
