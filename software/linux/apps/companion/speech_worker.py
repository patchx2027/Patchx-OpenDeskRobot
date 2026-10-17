"""Warm, serialized local speech models behind a private Unix socket."""
import argparse
import json
import os
from pathlib import Path
import select
import signal
import socket
import stat
import subprocess
import time
import sys
import threading
import wave

REQUEST_LIMIT = 16384


class SpeechWorker:
    def __init__(self, engine, socket_path, temp_root="/tmp"):
        self.engine = engine
        self.socket_path = Path(socket_path)
        self.temp_root = Path(temp_root).resolve()
        self.busy = threading.Lock()
        self.stop = threading.Event()
        self.server = None
        self.job = None

    def _path(self, value, input_file=False):
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError("Invalid speech path")
        path = Path(value)
        if not path.is_absolute() or path.name in ("", ".", ".."):
            raise ValueError("Invalid speech path")
        parent = path.parent
        info = parent.lstat()
        if (parent.parent != self.temp_root or not parent.name.startswith("rtctrl-voice-") or
                not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o700 or parent.resolve() != parent):
            raise ValueError("Speech path must be a private turn directory")
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError("Invalid speech file")
            if input_file and info.st_size > 2 * 1024 * 1024:
                raise ValueError("Speech input too large")
        elif input_file:
            raise ValueError("Speech input missing")
        return path

    @staticmethod
    def _send(peer, value):
        try:
            peer.sendall(json.dumps(value).encode() + b"\n")
        except OSError:
            pass

    @staticmethod
    def _connected(peer):
        ready, _, _ = select.select([peer], [], [], 0)
        if not ready:
            return True
        try:
            return bool(peer.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT))
        except OSError:
            return False

    def _request(self, peer):
        peer.settimeout(2)
        payload = bytearray()
        while b"\n" not in payload:
            block = peer.recv(min(4096, REQUEST_LIMIT + 1 - len(payload)))
            if not block:
                raise ValueError("Incomplete request")
            payload.extend(block)
            if len(payload) > REQUEST_LIMIT:
                raise ValueError("Request too large")
        line, remainder = bytes(payload).split(b"\n", 1)
        if remainder:
            raise ValueError("One request per connection")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("Request must be object")
        return value

    def _process(self, peer, request):
        if request.get("operation") == "tts_stream":
            return self._process_stream(peer, request)
        response = None
        try:
            operation = request.get("operation")
            output = self._path(request.get("output"))
            if operation == "asr":
                source = self._path(request.get("input"), input_file=True)
                result = self.engine.recognize(source)
                if not isinstance(result, str) or len(result) > 4096:
                    raise ValueError("Invalid recognition result")
            elif operation == "tts":
                text = request.get("text")
                if not isinstance(text, str) or not text.strip() or len(text) > 120:
                    raise ValueError("Invalid synthesis text")
                result = self.engine.synthesize(text)
            else:
                raise ValueError("Unsupported operation")
            if self.stop.is_set() or not self._connected(peer):
                return
            self._path(str(output))
            # No parent recreation: cancelled turns must stay deleted.
            descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "wb") as target:
                if operation == "asr":
                    target.write(json.dumps({"text": result}, ensure_ascii=False).encode())
                else:
                    pcm, rate = result
                    if rate not in (8000, 16000, 22050, 24000, 44100, 48000) or len(pcm) > rate * 2 * 45:
                        raise ValueError("Invalid synthesized audio")
                    with wave.open(target, "wb") as wav:
                        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                        wav.writeframes(pcm)
            response = {"ok": True}
        except Exception:
            response = {"ok": False, "error": "local speech operation failed"}
        finally:
            # Completion must mean the next serialized operation can start.
            # Sending first races an immediate ASR -> TTS request against release.
            self.busy.release()
            if response is not None:
                self._send(peer, response)
            peer.close()

    def _process_stream(self, peer, request):
        deadline = time.monotonic() + 120
        total = 0
        framing_clean = True
        response = {"type": "done", "ok": False, "error": "local speech operation failed"}
        def cancelled():
            return self.stop.is_set() or time.monotonic() >= deadline or not self._connected(peer)
        def write(data):
            view = memoryview(data)
            # Bound a stalled reader independently of the operation deadline.
            write_deadline = min(deadline, time.monotonic() + 5)
            while view:
                if cancelled() or time.monotonic() >= write_deadline:
                    raise TimeoutError("Speech stream cancelled or blocked")
                if not select.select([], [peer], [], .1)[1]:
                    continue
                try:
                    count = peer.send(view[:16384])
                except (BlockingIOError, socket.timeout):
                    continue
                if not count:
                    raise ConnectionError("Speech peer closed")
                view = view[count:]
        def audio(pcm, rate):
            nonlocal total, framing_clean
            if (rate != 44100 or not isinstance(pcm, bytes) or not pcm or len(pcm) % 2):
                raise ValueError("Invalid streamed PCM")
            total += len(pcm)
            if total > rate * 2 * 45:
                raise ValueError("Streamed PCM too long")
            framing_clean = False
            write(json.dumps({"type": "audio", "rate": rate, "bytes": len(pcm)}).encode() + b"\n")
            write(pcm)
            framing_clean = True
        try:
            text = request.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 120:
                raise ValueError("Invalid synthesis text")
            peer.setblocking(False)
            self.engine.synthesize_stream(text, audio, cancelled=cancelled)
            if not total or cancelled():
                raise ValueError("Empty or cancelled speech stream")
            response = {"type": "done", "ok": True}
        except Exception:
            pass
        finally:
            self.busy.release()
            # A partially sent frame cannot be repaired, but EOF fails closed.
            try:
                peer.settimeout(.2)
                if framing_clean:
                    self._send(peer, response)
            finally:
                peer.close()

    def serve(self):
        self.socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Reclaim only an owned stale socket, never a live worker or another file.
        if self.socket_path.exists() or self.socket_path.is_symlink():
            info = self.socket_path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError("Refusing unrelated speech socket path")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(.3)
                try:
                    probe.connect(str(self.socket_path))
                except ConnectionRefusedError:
                    current = self.socket_path.lstat()
                    if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                        raise ValueError("Speech socket changed")
                    self.socket_path.unlink()
                else:
                    raise ValueError("Speech worker already running")
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        owned_socket = self.socket_path.lstat()
        self.server.listen(4)
        self.server.settimeout(.2)
        print("speech worker ready", flush=True)
        try:
            while not self.stop.is_set():
                try:
                    peer, _ = self.server.accept()
                except socket.timeout:
                    continue
                try:
                    request = self._request(peer)
                    if request.get("operation") == "health":
                        self._send(peer, {"ok": True, "ready": True, "busy": self.busy.locked(),
                                          "asr_backend": getattr(self.engine, "asr_backend", "cpu"),
                                          "tts_backend": getattr(self.engine, "tts_backend", "cpu"),
                                          "tts_kind": getattr(self.engine, "tts_kind", "vits")})
                        peer.close()
                    elif request.get("operation") not in ("asr", "tts", "tts_stream"):
                        raise ValueError("Unsupported operation")
                    elif not self.busy.acquire(blocking=False):
                        self._send(peer, {"ok": False, "error": "local speech worker busy"})
                        peer.close()
                    else:
                        self.job = threading.Thread(target=self._process, args=(peer, request), daemon=True)
                        self.job.start()
                except Exception:
                    self._send(peer, {"ok": False, "error": "invalid speech request"})
                    peer.close()
        finally:
            self.server.close()
            try:
                current = self.socket_path.lstat()
                if (current.st_dev, current.st_ino, current.st_uid) == (owned_socket.st_dev, owned_socket.st_ino, os.getuid()):
                    self.socket_path.unlink()
            except FileNotFoundError:
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--asr-backend", choices=("cpu", "rknn"), default="cpu")
    parser.add_argument("--tts-kind", choices=("vits", "vits_aishell3", "melo", "melo_npu"), default="vits")
    parser.add_argument("--sid", type=int, default=0)
    parser.add_argument("--threads", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    try:
        engine = SherpaEngine(Path(args.root), args.tts_kind, args.sid, args.threads, args.asr_backend)
        worker = SpeechWorker(engine, args.socket)
        signal.signal(signal.SIGTERM, lambda *_: worker.stop.set())
        signal.signal(signal.SIGINT, lambda *_: worker.stop.set())
        worker.serve()
    except KeyboardInterrupt:
        pass
    except Exception:
        print("speech worker initialization failed", file=sys.stderr, flush=True)
        return 1
    return 0


# SherpaEngine is initialized only from main; tests use a small fake engine.

def pcm16_to_float(pcm):
    """Convert little-endian PCM without one Python object per sample."""
    import numpy as np
    values = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    values *= 1.0 / 32768
    return values


def float_to_pcm16(samples):
    """Preserve truncation and saturation of the original scalar conversion."""
    import numpy as np
    # Float64 matches the former float32-scalar * Python-int calculation.
    values = np.asarray(samples, dtype=np.float64) * 32767
    if not np.isfinite(values).all():
        raise ValueError("Invalid synthesized samples")
    np.clip(values, -32768, 32767, out=values)
    return values.astype("<i2").tobytes()


class RknnRecognizer:
    """Experimental fixed RV1126B runner; errors never silently fall back to CPU."""
    OUTPUT_LIMIT = 1024 * 1024

    def __init__(self, root, timeout=55):
        self.directory = Path(root).resolve() / "npu-asr"
        self.timeout = timeout

    def recognize(self, path):
        argv = [str(self.directory / "rknn_zipformer_demo"),
                "model/encoder.rknn", "model/decoder.rknn", "model/joiner.rknn",
                str(Path(path).resolve())]
        process = subprocess.Popen(argv, cwd=self.directory, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        output = bytearray()
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("NPU recognition timed out")
                readable, _, _ = select.select([process.stdout], [], [], min(remaining, .2))
                if not readable:
                    continue
                block = os.read(process.stdout.fileno(), 16384)
                if not block:
                    break
                output.extend(block)
                if len(output) > self.OUTPUT_LIMIT:
                    raise ValueError("NPU recognition output too large")
            process.wait(timeout=max(.001, deadline - time.monotonic()))
            if process.returncode:
                raise ValueError("NPU recognition failed")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            process.stdout.close()
        lines = output.decode("utf-8", errors="strict").splitlines()
        results = [line.partition("Zipformer output:")[2].strip()
                   for line in lines if line.startswith("Zipformer output:")]
        if len(results) != 1 or not results[0] or len(results[0]) > 4096:
            raise ValueError("Missing or invalid NPU recognition result")
        return results[0]


class SherpaEngine:
    def __init__(self, root, tts_kind="vits", sid=0, threads=2, asr_backend="cpu"):
        if asr_backend not in ("cpu", "rknn"):
            raise ValueError("Unsupported ASR backend")
        self.asr_backend = asr_backend
        self.rknn = RknnRecognizer(root) if asr_backend == "rknn" else None
        if type(threads) is not int or threads not in (1, 2):
            raise ValueError("Speech threads must be 1 or 2")
        if (tts_kind not in ("vits", "vits_aishell3", "melo", "melo_npu") or type(sid) is not int or
                not 0 <= sid < (1 if tts_kind in ("melo", "melo_npu") else 174)):
            raise ValueError("Unsupported local voice model or speaker")
        self.tts_kind = tts_kind
        sys.path.insert(0, str(root / "python"))
        import sherpa_onnx
        asr_root = root / "sherpa-onnx-zipformer-ctc-small-zh-int8-2025-07-16"
        tts_root = root / ("vits-melo-tts-zh_en" if tts_kind in ("melo", "melo_npu") else "vits-icefall-zh-aishell3")
        vits_options = {"dict_dir": str(tts_root / "dict")} if tts_kind in ("melo", "melo_npu") else {}
        rules = ("date.fst", "number.fst", "phone.fst")
        if tts_kind not in ("melo", "melo_npu"):
            rules += ("new_heteronym.fst",)
        self.asr = None
        if asr_backend == "cpu":
            self.asr = sherpa_onnx.OfflineRecognizer.from_zipformer_ctc(
                model=str(asr_root / "model.int8.onnx"), tokens=str(asr_root / "tokens.txt"), num_threads=threads)
        self.tts_backend = "rknn" if tts_kind == "melo_npu" else "cpu"
        if tts_kind == "melo_npu":
            from .melo_npu import MeloNpu
            self.tts = MeloNpu(root, sherpa_onnx, threads)
        else:
            self.tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
                model=sherpa_onnx.OfflineTtsModelConfig(vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=str(tts_root / "model.onnx"), lexicon=str(tts_root / "lexicon.txt"),
                    tokens=str(tts_root / "tokens.txt"), **vits_options), num_threads=threads),
                rule_fsts=",".join(str(tts_root / name) for name in
                                  rules)))
        self.sid = sid

    def recognize(self, path):
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as source:
            with wave.open(source, "rb") as wav:
                if (wav.getnchannels() != 1 or wav.getsampwidth() != 2 or
                        wav.getframerate() != 16000 or wav.getnframes() > 16000 * 60):
                    raise ValueError("Unsupported recognition WAV")
                pcm = wav.readframes(wav.getnframes())
                if len(pcm) != wav.getnframes() * 2 or not pcm:
                    raise ValueError("Incomplete recognition WAV")
        if self.rknn is not None:
            return self.rknn.recognize(path)
        samples = pcm16_to_float(pcm)
        stream = self.asr.create_stream()
        stream.accept_waveform(16000, samples)
        self.asr.decode_stream(stream)
        text = stream.result.text
        del stream
        return text

    def synthesize_stream(self, text, on_audio, cancelled=None):
        if self.tts_kind != "melo_npu":
            raise ValueError("Streaming synthesis requires Melo NPU")
        def emit(samples, rate):
            on_audio(float_to_pcm16(samples), rate)
        return self.tts.synthesize_stream(text, emit, cancelled=cancelled)

    def synthesize(self, text):
        if self.tts_kind == "melo_npu":
            samples, rate = self.tts.synthesize(text)
            return float_to_pcm16(samples), rate
        generated = self.tts.generate(text, sid=self.sid, speed=1.0)
        samples = generated.samples
        if len(samples) > generated.sample_rate * 45:
            raise ValueError("Synthesized reply too long")
        return float_to_pcm16(samples), generated.sample_rate


if __name__ == "__main__":
    sys.exit(main())
