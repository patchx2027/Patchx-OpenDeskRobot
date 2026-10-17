"""Local ASR/TTS subprocesses with text-only, bounded Qianfan HTTPS inference.

Commands exchange private temporary files, never shell fragments. Lightweight
clients call the resident speech worker; model inference is serialized.
"""
import http.client
import json
import os
import queue
import select
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from urllib.parse import urlsplit

from .audio import OpusCodec, FRAME_BYTES, PcmAudio, PcmStreamChunk


class LocalVoiceTransport:
    def __init__(self, config, on_message, on_error):
        self.config = dict(config)
        self.on_message, self.on_error = on_message, on_error
        self._lock = threading.RLock()
        self._generation = 0
        self._closed = True
        self._pcm = bytearray()
        self._recording = False
        self._process = None
        self._worker = None
        self._listener = None
        self._duplex = False
        self._asr_retired = False
        self._codec = None
        self._asr_stream = None
        self._frames = None
        self._http = None
        self._http_abort = None
        self._speech_peer = None
        self._session = uuid.uuid4().hex

    @staticmethod
    def _command(value):
        if (not isinstance(value, list) or not value or len(value) > 64 or
                any(not isinstance(x, str) or not x or len(x) > 4096 or "\x00" in x for x in value) or
                not os.path.isabs(value[0]) or not os.access(value[0], os.X_OK)):
            raise ValueError("Local speech command requires an absolute executable and bounded argument list")
        return value

    def connect(self):
        worker_socket = self.config.get("local_speech_socket", "")
        if worker_socket:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                    peer.settimeout(1)
                    peer.connect(worker_socket)
                    peer.sendall(b'{"operation":"health"}\n')
                    response = peer.recv(4096)
                    health = json.loads(response)
                    if not health.get("ok") or not health.get("ready"):
                        raise ValueError("Worker not ready")
            except (OSError, ValueError):
                raise ValueError("本地语音模型仍在加载，请稍后重试") from None
            if health.get("busy"):
                raise ValueError("本地模型仍在处理上一轮，请稍后重试")
        self._command(self.config.get("local_asr_command"))
        self._command(self.config.get("local_tts_command"))
        name = self.config.get("qianfan_token_env", "BAIDU_QIANFAN_API_KEY")
        token = os.environ.get(name, "")
        if not token or len(token) > 4096 or "\n" in token or "\r" in token:
            raise ValueError("Qianfan API key environment variable is missing or invalid")
        with self._lock:
            if not self._closed:
                raise RuntimeError("Local voice already connected")
            if ((self._listener and self._listener.is_alive()) or
                    (self._worker and self._worker.is_alive())):
                raise RuntimeError("Previous local voice task is still stopping")
            if self.config.get("local_asr_streaming"):
                from .streaming_asr import StreamingAsr
                self._asr_stream = StreamingAsr(self.config["local_speech_root"])
                self._asr_retired = False
            self._codec = OpusCodec()
            self._closed = False

    def _active(self, generation):
        return not self._closed and generation == self._generation

    def _emit(self, generation, value):
        with self._lock:
            if self._active(generation):
                if isinstance(value, dict):
                    value["session_id"] = self._session
                self.on_message(value)

    def send(self, value):
        with self._lock:
            if self._closed:
                raise RuntimeError("Local voice is closed")
            if isinstance(value, bytes):
                if self._recording:
                    pcm = self._codec.decode(value)
                    if self._asr_stream:
                        self.send_pcm(pcm)
                        return
                    limit = int(min(60, self.config.get("max_listen_s", 15)) * 32000)
                    if len(self._pcm) + len(pcm) > limit + 3840:
                        raise ValueError("Local recording exceeds configured duration")
                    self._pcm.extend(pcm)
                return
            kind, state = value.get("type"), value.get("state")
            if kind == "hello":
                self._emit(self._generation, {"type": "hello", "audio_params": {
                    "format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60}})
            elif kind == "abort":
                self._cancel()
            elif kind == "reply":
                text = value.get("text")
                if not self._duplex or not self._recording:
                    raise RuntimeError("Duplex listener is not active")
                if not isinstance(text, str) or not text.strip() or len(text) > 4096:
                    raise ValueError("Invalid duplex reply text")
                if self._worker is not None:
                    raise RuntimeError("Previous reply is still running")
                self._worker = threading.Thread(target=self._duplex_reply,
                    args=(self._generation, text), name="companion-duplex-reply", daemon=True)
                self._worker.start()
            elif kind == "listen" and state == "start":
                if self._listener and self._listener.is_alive():
                    raise RuntimeError("Previous duplex listener is still stopping")
                if self._worker and self._worker.is_alive():
                    raise RuntimeError("Previous local voice task is still stopping")
                self._cancel()
                self._duplex = bool(self.config.get("full_duplex") and value.get("mode") == "auto")
                if self._asr_retired:
                    from .streaming_asr import StreamingAsr
                    self._asr_stream = StreamingAsr(self.config["local_speech_root"])
                    self._asr_retired = False
                if self._duplex and not self._asr_stream:
                    raise RuntimeError("Duplex requires streaming ASR")
                self._codec.reset_decoder()
                self._recording = True
                if self._asr_stream:
                    self._frames = queue.Queue(maxsize=32)
                    if self._duplex:
                        self._listener = threading.Thread(target=self._duplex_listen,
                            args=(self._generation, self._frames, self._asr_stream),
                            name="companion-duplex-asr", daemon=True)
                        self._listener.start()
                        return
                    self._worker = threading.Thread(target=self._stream_turn,
                        args=(self._generation, self._frames, value.get("mode") == "auto"),
                        name="companion-stream-asr", daemon=True)
                    self._worker.start()
            elif kind == "listen" and state == "stop" and self._recording:
                self._recording = False
                if self._asr_stream:
                    self._frames.put_nowait(None)
                    return
                pcm = bytes(self._pcm)
                self._pcm.clear()
                generation = self._generation
                self._worker = threading.Thread(target=self._run, args=(generation, pcm),
                                                name="companion-local-voice", daemon=True)
                self._worker.start()

    def send_pcm(self, pcm):
        """Local-only PCM path: avoid Opus encode/decode before recognition."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Local voice is closed")
            if self._recording and self._asr_stream:
                if not isinstance(pcm, bytes) or not pcm or len(pcm) > FRAME_BYTES or len(pcm) % 2:
                    raise ValueError("Invalid local PCM frame")
                self._frames.put_nowait(pcm)

    def _stream_turn(self, generation, frames, automatic):
        from .speech_gate import SpeechGate
        gate = SpeechGate() if automatic else None
        recognizing = not automatic
        try:
            if recognizing:
                self._asr_stream.exchange(3)
            else:
                self._emit(generation, {"type": "asr_waiting"})
            previous = ""
            first_partial = False
            started = last_frame = time.monotonic()
            while self._active(generation):
                try:
                    pcm = frames.get(timeout=.2)
                except queue.Empty:
                    if time.monotonic() - last_frame > 5:
                        raise TimeoutError("No capture progress")
                    continue
                if pcm is None:
                    break
                last_frame = time.monotonic()
                packets = [pcm]
                if not recognizing:
                    packets = gate.feed(pcm)
                    if packets is None:
                        continue
                    self._asr_stream.exchange(3)
                    recognizing = True
                    started = time.monotonic()
                    self._emit(generation, {"type": "asr_started"})
                endpoint = False
                for packet in packets:
                    if not self._active(generation):
                        return
                    result = self._asr_stream.exchange(1, packet)
                    text = result.get("text", "")
                    if text != previous:
                        if text and not first_partial:
                            first_partial = True
                            self._timing(generation, "asr_first_partial_ms", started)
                        previous = text
                        self._emit(generation, {"type": "stt", "text": text, "partial": True})
                    if automatic and result.get("endpoint"):
                        if not text.strip():
                            # False onset/noise: return to lightweight waiting without
                            # closing capture, finalizing, or starting a cloud turn.
                            recognizing = False
                            gate = SpeechGate()
                            first_partial = False
                            previous = ""
                            self._emit(generation, {"type": "asr_waiting"})
                        else:
                            endpoint = True
                        break
                if endpoint:
                    with self._lock:
                        if not self._active(generation):
                            return
                        self._recording = False
                    self._emit(generation, {"type": "asr_endpoint"})
                    break
            if not self._active(generation):
                return
            if not recognizing:
                self._emit(generation, {"type": "asr_empty"})
                return
            finalize_started = time.monotonic()
            text = self._asr_stream.exchange(2).get("text", "").strip()
            self._timing(generation, "asr_finalize_ms", finalize_started)
            if not text:
                self._emit(generation, {"type": "asr_empty"})
                return
            self._run(generation, None, recognized_text=text)
        except Exception:
            with self._lock:
                if self._active(generation):
                    self.on_error("流式识别失败或超时，已停止采音，请重新准备语音")

    def _duplex_reply(self, generation, text):
        try:
            self._run(generation, None, recognized_text=text)
        finally:
            with self._lock:
                if self._worker is threading.current_thread():
                    self._worker = None
                self._emit(generation, {"type": "response_complete"})

    def _duplex_listen(self, generation, frames, stream):
        from .speech_gate import SpeechGate
        gate = SpeechGate()
        recognizing = False
        previous = ""
        total = 0
        started = last_frame = time.monotonic()
        limit = 29 * 32000
        try:
            self._emit(generation, {"type": "duplex_waiting"})
            while self._active(generation):
                try:
                    pcm = frames.get(timeout=.2)
                except queue.Empty:
                    if time.monotonic() - last_frame > 5:
                        raise TimeoutError("No capture progress")
                    continue
                stopping = pcm is None
                packets = [] if stopping else [pcm]
                if not stopping:
                    last_frame = time.monotonic()
                    if not recognizing:
                        packets = gate.feed(pcm)
                        if packets is None:
                            continue
                        stream.exchange(3)
                        recognizing = True
                        total = 0
                        previous = ""
                        started = time.monotonic()
                        self._emit(generation, {"type": "duplex_started"})
                endpoint = stopping and recognizing
                empty_endpoint = False
                for packet in packets:
                    if not self._active(generation):
                        return
                    packet = packet[:limit - total]
                    if packet:
                        result = stream.exchange(1, packet)
                        total += len(packet)
                        text = result.get("text", "")
                        if text != previous:
                            previous = text
                            self._emit(generation, {"type": "duplex_partial", "text": text})
                        endpoint = bool(result.get("endpoint"))
                        empty_endpoint = endpoint and not text.strip()
                    endpoint = endpoint or total >= limit or time.monotonic() - started >= 29
                    if endpoint:
                        break
                if endpoint:
                    if not self._active(generation):
                        return
                    if not empty_endpoint:
                        text = stream.exchange(2).get("text", "").strip()
                        if text:
                            self._emit(generation, {"type": "duplex_final", "text": text})
                    recognizing = False
                    gate = SpeechGate()
                    if not stopping:
                        self._emit(generation, {"type": "duplex_waiting"})
                if stopping:
                    return
        except Exception:
            with self._lock:
                if self._active(generation):
                    self._cancel()
                    self.on_error("流式识别失败或超时，已停止采音，请重新准备语音")
        finally:
            with self._lock:
                if self._listener is threading.current_thread():
                    self._listener = None

    @staticmethod
    def _terminate(process):
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)

    def _cancel(self):
        self._generation += 1
        if self._duplex and self._asr_stream and not self._asr_retired:
            self._asr_stream.close()
            self._asr_retired = True
        self._recording = False
        self._pcm.clear()
        if self._frames:
            try:
                self._frames.put_nowait(None)
            except queue.Full:
                pass
        self._terminate(self._process)
        if self._http_abort:
            self._http_abort()
        if self._http:
            if self._http.sock:
                try:
                    self._http.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            self._http.close()
        if self._speech_peer:
            self._speech_peer.close()

    def close(self):
        with self._lock:
            self._closed = True
            self._cancel()
            if self._asr_stream:
                self._asr_stream.close()
            if self._codec:
                self._codec.close()
                self._codec = None

    def _execute(self, generation, template, timeout_key, **values):
        command = [item.format(**values) for item in self._command(template)]
        with self._lock:
            if not self._active(generation):
                raise RuntimeError("Local voice cancelled")
            # Do not pass cloud credentials to model helpers.
            env = {key: os.environ[key] for key in ("PATH", "LANG", "LD_LIBRARY_PATH") if key in os.environ}
            self._process = process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, start_new_session=True)
        try:
            process.wait(timeout=min(120, max(1, self.config.get(timeout_key, 90))))
            if process.returncode:
                raise RuntimeError("Local speech model failed; check model installation")
        except subprocess.TimeoutExpired:
            self._terminate(process)
            raise RuntimeError("Local speech model timed out") from None
        finally:
            # A helper must not leave model children alive after its leader exits.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            with self._lock:
                if self._process is process:
                    self._process = None
        if not self._active(generation):
            raise RuntimeError("Local voice cancelled")

    def _reply(self, text):
        token = os.environ.get(self.config.get("qianfan_token_env", "BAIDU_QIANFAN_API_KEY"), "")
        payload = json.dumps({"model": self.config.get("qianfan_model", "ernie-4.5-turbo-32k"),
            "messages": [{"role": "system", "content": "请用自然口语中文回答，默认一到两句话、最多60个汉字，不使用Markdown。"},
                         {"role": "user", "content": text}], "max_tokens": 192,
            "stream": False}, ensure_ascii=False).encode("utf-8")
        proxy = self.config.get("qianfan_proxy_url", "")
        if proxy:
            parsed = urlsplit(proxy)
            if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost") or
                    not parsed.port or parsed.username or parsed.password or parsed.path not in ("", "/") or
                    parsed.query or parsed.fragment):
                raise RuntimeError("本地语音：千帆代理配置无效")
            connection = http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=30)
            connection.set_tunnel("qianfan.baidubce.com", 443)
        else:
            connection = http.client.HTTPSConnection("qianfan.baidubce.com", timeout=30)
        with self._lock:
            if self._closed:
                connection.close()
                raise RuntimeError("Local voice cancelled")
            self._http = connection
        try:
            connection.request("POST", "/v2/chat/completions", body=payload,
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
            response = connection.getresponse()
            raw = response.read(65537)
            if response.status != 200 or len(raw) > 65536:
                raise RuntimeError("Qianfan request failed; check network, quota and API authorization")
            reply = json.loads(raw)["choices"][0]["message"]["content"]
            if not isinstance(reply, str) or not reply.strip() or len(reply) > 4096:
                raise ValueError("Invalid Qianfan reply")
            return reply.strip()[:120]
        except (OSError, ValueError, KeyError, IndexError, http.client.HTTPException):
            raise RuntimeError("Qianfan returned no usable reply") from None
        finally:
            connection.close()
            with self._lock:
                if self._http is connection:
                    self._http = None

    def _timing(self, generation, name, started):
        self._emit(generation, {"type": "latency", "values": {
            name: round((time.monotonic() - started) * 1000, 1)}})

    def _stream_tts(self, generation, text, stream=None):
        own_stream = stream is None
        if own_stream:
            stream = {"started": time.monotonic(), "playing": False, "first_pcm": False,
                      "rate": None, "total": 0, "pending": []}
        started = stream["started"]
        deadline = started + min(120, self.config.get("local_tts_timeout_s", 90))
        peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with self._lock:
            if not self._active(generation):
                peer.close()
                return
            self._speech_peer = peer
        def read_exact(count):
            parts = bytearray()
            while len(parts) < count:
                if not self._active(generation):
                    raise RuntimeError("Synthesis cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Synthesis stream timeout")
                if not select.select([peer], [], [], min(.2, remaining))[0]:
                    continue
                block = peer.recv(min(65536, count - len(parts)))
                if not block:
                    raise RuntimeError("Synthesis stream closed before completion")
                parts.extend(block)
            return bytes(parts)
        total = 0
        rate = None
        chunks = 0
        pending = stream["pending"]
        try:
            peer.settimeout(2)
            peer.connect(self.config["local_speech_socket"])
            peer.sendall(json.dumps({"operation": "tts_stream", "text": text}, ensure_ascii=False).encode() + b"\n")
            peer.setblocking(False)
            while True:
                header = bytearray()
                while not header.endswith(b"\n"):
                    header.extend(read_exact(1))
                    if len(header) > 4096:
                        raise ValueError("Synthesis header too large")
                message = json.loads(header)
                if not isinstance(message, dict):
                    raise ValueError("Invalid synthesis header")
                if message.get("type") == "done":
                    if message.get("ok") is not True or not total:
                        raise RuntimeError("Synthesis stream failed")
                    if pending and own_stream:
                        self._deliver_tts(generation, stream)
                    if own_stream:
                        self._finish_tts(generation, stream)
                    return
                size, next_rate = message.get("bytes"), message.get("rate")
                if (message.get("type") != "audio" or type(size) is not int or size <= 0 or size % 2 or
                        type(next_rate) is not int or next_rate != 44100 or
                        (rate is not None and rate != next_rate) or total + size > next_rate * 2 * 45 or chunks >= 128):
                    raise ValueError("Invalid synthesis chunk")
                pcm = read_exact(size)
                if not stream["first_pcm"]:
                    self._timing(generation, "tts_first_pcm_ms", started)
                    stream["first_pcm"] = True
                if stream["rate"] is not None and stream["rate"] != next_rate:
                    raise ValueError("Synthesis sample rate changed")
                stream["rate"] = next_rate
                stream["total"] += size
                if stream["total"] > next_rate * 2 * 45:
                    raise ValueError("Synthesis reply too large")
                rate = next_rate
                total += size
                chunks += 1
                pending.append((text if chunks == 1 else None, pcm))
                # A tiny greeting can finish before the next batch is ready.
                # Keep a bounded startup cushion instead of emitting it into a gap.
                if stream["playing"] or stream["total"] >= rate * 2 * self.config.get("local_tts_prebuffer_s", 2.):
                    self._deliver_tts(generation, stream)
        finally:
            peer.close()
            with self._lock:
                if self._speech_peer is peer:
                    self._speech_peer = None

    def _deliver_tts(self, generation, stream):
        if not stream["playing"]:
            self._timing(generation, "tts_first_audio_ms", stream["started"])
            self._emit(generation, {"type": "tts", "state": "start"})
            stream["playing"] = True
        for sentence, block in stream["pending"]:
            if sentence:
                self._emit(generation, {"type": "tts", "state": "sentence_start", "text": sentence})
            self._emit(generation, PcmStreamChunk(block, stream["rate"], False))
        stream["pending"].clear()

    def _finish_tts(self, generation, stream):
        if not stream["first_pcm"]:
            raise ValueError("No synthesized audio")
        if stream["pending"]:
            self._deliver_tts(generation, stream)
        self._emit(generation, PcmStreamChunk(b"", stream["rate"], True))
        self._timing(generation, "tts_total_ms", stream["started"])
        self._emit(generation, {"type": "tts", "state": "stop"})

    def _reply_deltas(self, generation, text, cancelled):
        from .cloud_stream import content_deltas
        token = os.environ.get(self.config.get("qianfan_token_env", "BAIDU_QIANFAN_API_KEY"), "")
        payload = json.dumps({"model": self.config.get("qianfan_model", "ernie-4.5-turbo-32k"),
            "messages": [{"role": "system", "content": "请用自然口语中文回答，默认一到两句话、最多60个汉字，不使用Markdown。"},
                         {"role": "user", "content": text}], "max_tokens": 192,
            "stream": True}, ensure_ascii=False).encode("utf-8")
        proxy = self.config.get("qianfan_proxy_url", "")
        if proxy:
            parsed = urlsplit(proxy)
            if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost") or
                    not parsed.port or parsed.username or parsed.password or parsed.path not in ("", "/") or
                    parsed.query or parsed.fragment):
                raise RuntimeError("Invalid cloud proxy")
            connection = http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=8)
            connection.set_tunnel("qianfan.baidubce.com", 443)
        else:
            connection = http.client.HTTPSConnection("qianfan.baidubce.com", timeout=8)
        deadline = time.monotonic() + 45
        def active():
            if cancelled.is_set() or not self._active(generation):
                return False
            if time.monotonic() > deadline:
                raise TimeoutError("Cloud stream timed out")
            return True
        with self._lock:
            if not active():
                connection.close()
                return
            self._http = connection
        network_socket = []
        def expire():
            live_socket = network_socket[0] if network_socket else connection.sock
            if live_socket:
                try:
                    live_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            connection.close()
        with self._lock:
            self._http_abort = expire
        timer = threading.Timer(45, expire)
        timer.daemon = True
        timer.start()
        try:
            connection.connect()
            network_socket.append(connection.sock)
            if not active():
                return
            connection.request("POST", "/v2/chat/completions", body=payload,
                headers={"Content-Type": "application/json", "Accept": "text/event-stream",
                         "Authorization": "Bearer " + token})
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError("Cloud streaming request failed")
            yield from content_deltas(response, active)
        finally:
            timer.cancel()
            connection.close()
            with self._lock:
                if self._http_abort is expire:
                    self._http_abort = None
                if self._http is connection:
                    self._http = None

    def _stream_reply(self, generation, text):
        from .cloud_stream import SentenceBuffer
        sentences = queue.Queue(maxsize=4)
        cancelled = threading.Event()
        completed = threading.Event()
        errors = []
        cloud_started = time.monotonic()
        def put(sentence):
            while self._active(generation) and not cancelled.is_set():
                try:
                    sentences.put(sentence, timeout=.1)
                    return
                except queue.Full:
                    pass
            raise RuntimeError("Cloud stream cancelled")
        def produce():
            reply = ""
            first_sentence = True
            splitter = SentenceBuffer()
            deltas = self._reply_deltas(generation, text, cancelled)
            try:
                for delta in deltas:
                    if not reply:
                        self._timing(generation, "cloud_first_token_ms", cloud_started)
                    delta = delta[:max(0, 120 - len(reply))]
                    reply += delta
                    ready = splitter.feed(delta)
                    if ready:
                        self._emit(generation, {"type": "llm", "text": reply, "emotion": "neutral"})
                    for sentence in ready:
                        if first_sentence:
                            self._timing(generation, "cloud_first_sentence_ms", cloud_started)
                            first_sentence = False
                        put(sentence)
                    if len(reply) >= 120:
                        break
                if not reply.strip():
                    raise ValueError("Empty cloud reply")
                self._emit(generation, {"type": "llm", "text": reply, "emotion": "neutral"})
                for sentence in splitter.feed("", final=True):
                    if first_sentence:
                        self._timing(generation, "cloud_first_sentence_ms", cloud_started)
                        first_sentence = False
                    put(sentence)
                self._timing(generation, "cloud_ms", cloud_started)
            except Exception:
                errors.append(True)
            finally:
                deltas.close()
                completed.set()
        producer = threading.Thread(target=produce, name="companion-cloud-stream", daemon=True)
        producer.start()
        stream = None
        try:
            while self._active(generation):
                if errors:
                    raise RuntimeError("Cloud streaming failed")
                try:
                    sentence = sentences.get(timeout=.1)
                except queue.Empty:
                    if completed.is_set():
                        break
                    continue
                if stream is None:
                    stream = {"started": time.monotonic(), "playing": False, "first_pcm": False,
                              "rate": None, "total": 0, "pending": []}
                self._stream_tts(generation, sentence, stream)
            if errors:
                raise RuntimeError("Cloud streaming failed")
            if self._active(generation) and stream is not None:
                self._finish_tts(generation, stream)
        finally:
            cancelled.set()
            with self._lock:
                if self._http_abort:
                    self._http_abort()
                connection = self._http
                if connection and connection.sock:
                    try:
                        connection.sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                if connection:
                    connection.close()
            # Keep the parent worker owned until the finite network deadline exits;
            # a new turn must never race a retired cloud reader.
            producer.join(timeout=46)
            if producer.is_alive():
                raise RuntimeError("Cloud reader did not stop")

    def _run(self, generation, pcm, recognized_text=None):
        stage = "录音"
        try:
            if recognized_text is None and not pcm:
                raise RuntimeError("No local microphone audio received")
            with tempfile.TemporaryDirectory(prefix="rtctrl-voice-") as directory:
                source, result, speech = [str(Path(directory) / name) for name in ("input.wav", "asr.json", "reply.wav")]
                text = recognized_text
                if text is None:
                    with wave.open(source, "wb") as output:
                        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                        output.writeframes(pcm)
                    del pcm
                    stage = "本地语音识别"
                    self._execute(generation, self.config["local_asr_command"], "local_asr_timeout_s", input=source, output=result, text="")
                    if Path(result).stat().st_size > 16384:
                        raise ValueError("Local ASR result too large")
                    text = json.loads(Path(result).read_text(encoding="utf-8"))["text"]
                if not isinstance(text, str) or not text.strip() or len(text) > 4096:
                    raise RuntimeError("Local ASR did not recognize speech")
                self._emit(generation, {"type": "stt", "text": text.strip(), "partial": False})
                if not self._active(generation):
                    return
                stage = "千帆文字回答"
                if self.config.get("local_tts_streaming") and self.config.get("qianfan_streaming", True):
                    stage = "流式回答与语音合成"
                    self._stream_reply(generation, text.strip())
                    return
                cloud_started = time.monotonic()
                reply = self._reply(text.strip())
                self._timing(generation, "cloud_ms", cloud_started)
                self._emit(generation, {"type": "llm", "text": reply, "emotion": "neutral"})
                stage = "本地语音合成"
                if self.config.get("local_tts_streaming"):
                    self._stream_tts(generation, reply)
                    return
                tts_started = time.monotonic()
                self._execute(generation, self.config["local_tts_command"], "local_tts_timeout_s", input=source, output=speech, text=reply)
                self._timing(generation, "tts_first_audio_ms", tts_started)
                self._timing(generation, "tts_total_ms", tts_started)
                self._emit(generation, {"type": "tts", "state": "start"})
                self._emit(generation, {"type": "tts", "state": "sentence_start", "text": reply})
                stage = "本地音频输出"
                self._play(generation, speech)
                self._emit(generation, {"type": "tts", "state": "stop"})
        except Exception as error:
            with self._lock:
                if self._active(generation):
                    # Never surface response bodies, command arguments or user text.
                    self.on_error(stage + "失败或超时，请检查配置、模型和网络")

    def _play(self, generation, filename):
        with wave.open(filename, "rb") as source:
            rate = source.getframerate()
            if (source.getnchannels() != 1 or source.getsampwidth() != 2 or
                    rate not in (8000, 16000, 22050, 24000, 44100, 48000) or
                    source.getnframes() > rate * 45):
                raise ValueError("Unsupported or oversized synthesized WAV")
            # Own the bytes before the turn directory is removed. Playback uses
            # ALSA backpressure at the original rate, with no lossy codec stage.
            pcm = source.readframes(source.getnframes())
            audio = PcmAudio(pcm, rate)
            audio.validate()
            self._emit(generation, audio)
