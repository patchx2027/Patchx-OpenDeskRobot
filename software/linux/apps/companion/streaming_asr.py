"""Bounded client for the persistent RV1126B Zipformer streaming runner.

Only the transport's ASR thread calls exchange(); close() may cancel it from
another thread. No model subprocess inherits cloud credentials or writes audio.
"""
import json
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import time


class StreamingAsr:
    def __init__(self, root):
        directory = Path(root).resolve() / "npu-asr"
        env = {key: os.environ[key] for key in ("PATH", "LANG", "LD_LIBRARY_PATH") if key in os.environ}
        self.process = subprocess.Popen(
            [str(directory / "rknn_zipformer_stream"), "model/encoder.rknn",
             "model/decoder.rknn", "model/joiner.rknn", "model/vocab.txt"], cwd=directory,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, start_new_session=True, bufsize=0)
        self.buffer = bytearray()
        os.set_blocking(self.process.stdin.fileno(), False)
        try:
            if self._read(time.monotonic() + 8).get("type") != "ready":
                raise ValueError("Streaming recognizer did not initialize")
        except Exception:
            self.close()
            raise

    def _read(self, deadline):
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Streaming recognizer timed out")
            if not select.select([self.process.stdout], [], [], min(.2, remaining))[0]:
                continue
            block = os.read(self.process.stdout.fileno(), 4096)
            if not block:
                raise RuntimeError("Streaming recognizer exited")
            self.buffer.extend(block)
            if len(self.buffer) > 16384:
                raise ValueError("Streaming result too large")
        line, self.buffer = self.buffer.split(b"\n", 1)
        value = json.loads(line)
        if not isinstance(value, dict) or value.get("type") == "error":
            raise ValueError("Invalid streaming result")
        if "endpoint" in value and type(value["endpoint"]) is not bool:
            raise ValueError("Invalid streaming endpoint")
        text = value.get("text", "")
        if not isinstance(text, str) or len(text) > 4096:
            raise ValueError("Invalid streaming transcript")
        return value

    def exchange(self, opcode, pcm=b""):
        if opcode not in (1, 2, 3) or (opcode != 1 and pcm) or len(pcm) > 32000 or len(pcm) % 2:
            raise ValueError("Invalid streaming frame")
        # Length covers payload only, followed by opcode and payload.
        packet = memoryview(struct.pack("<IB", len(pcm), opcode) + pcm)
        deadline = time.monotonic() + 5
        while packet:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Streaming write timed out")
            if not select.select([], [self.process.stdin], [], min(.2, remaining))[1]:
                continue
            try:
                count = os.write(self.process.stdin.fileno(), packet)
                packet = packet[count:]
            except BlockingIOError:
                pass
        value = self._read(deadline)
        if value.get("type") != {1: "partial", 2: "final", 3: "reset"}[opcode]:
            raise ValueError("Unexpected streaming response")
        return value

    def close(self):
        process = self.process
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
        # Do not close descriptors while another thread is using select/read;
        # process exit wakes the reader. Popen owns their final cleanup.
