"""Serialized companion state machine; all device work stays outside realtime control."""
import json
import math
import queue
import struct
import threading
import time
from collections import deque
from .audio import PcmAudio, PcmStreamChunk
from .echo_guard import EchoGuard
from .diagnostics import Diagnostics

AUDIO_PARAMS = {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60}
SILENCE_FRAME = bytes(1920)
SILENCE_FRAME_INTERVAL = 0.06
SILENCE_TAIL_FRAMES = 20
EMOTIONS = {"neutral", "happy", "sad", "angry", "surprised", "excited", "thinking", "sleepy"}

class Companion:
    def __init__(self, config, audio_factory=None, codec_factory=None, transport_factory=None):
        self.config = config
        self.diagnostics = Diagnostics()
        self.diagnostics.emit("service_started")
        self.audio_factory = audio_factory
        self.codec_factory = codec_factory
        self.transport_factory = transport_factory
        self.events = queue.Queue(maxsize=48)
        self.overflow = threading.Event()
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.audio = self.codec = self.transport = None
        self.generation = 0
        self.capture_epoch = 0
        self.capture_stopped_at = 0.
        self.audio_queued_at = 0.
        self.auto_until = 0.
        self.auto_due = 0.
        self.echo_sequence = 0
        self.echo_guard = EchoGuard()
        self.last_playback_at = -1000.
        self.echo_refresh_at = 0.
        self.duplex_overlap = False
        self.duplex_echo_suspected = False
        self.pending_utterances = deque()
        self.response_busy = False
        self.response_produced = False
        self.turn_audio_frames_sent = 0
        self.turn_audio_frames_received = 0
        self.silence_remaining = 0
        self.silence_due = 0.
        self.session = ""
        self.deadline = 0.
        self.demo_due = 0.
        self.demo_phase = 0
        self.tts_ended = False
        self.accept_audio = False
        self.data = {
            "control_mode": "device" if config.get("standalone_voice", False) else "browser",
            "input_state": "off", "next_transcript": "", "pending_utterances": 0, "echo_suspect": "", "echo_suspect_id": 0,
            "last_latency_ms": {}, "latency_ms": {}, "state": "offline", "emotion": "neutral", "transcript": "", "reply": "",
            "error": "", "connected": False, "muted": True, "continuous": False, "transcript_partial": False, "voice_progress": "idle",
            "face": {"available": False, "reason": "尚未连接视觉服务"},
            "metrics": {"audio_frames_sent": 0, "audio_frames_received": 0, "queue_overflows": 0,
                        "capture_peak_amplitude": 0, "echo_suspicions": 0},
            "capabilities": {"mode": config["mode"], "voice": "push-to-talk",
                "streaming_tts": config.get("local_tts_streaming", False),
                "streaming_asr": config.get("local_asr_streaming", False),
                "automatic_endpoint": config.get("local_asr_streaming", False),
                "wake_word": False, "aec": config.get("aec_enabled", False), "automatic_barge_in": False, "full_duplex": config.get("full_duplex", False),
                "actuator_control": False, "face": "external-read-only",
                "voice_backend": config.get("voice_backend", "android"),
                "local_asr_backend": config.get("local_asr_backend", "cpu"),
                "local_tts_backend": "rknn" if config.get("local_tts_kind") == "melo_npu" else "cpu",
                "local_tts_kind": config.get("local_tts_kind", "vits"),
                "audio_upload": config["mode"] == "live" and config.get("voice_backend", "android") != "local"},
        }

    def start(self):
        self.thread = threading.Thread(target=self._run, name="companion", daemon=True)
        self.thread.start()

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.data))

    def _set(self, **values):
        with self.lock:
            previous = self.data.get("state")
            self.data.update(values)
        state = values.get("state")
        if state and state != previous:
            self.diagnostics.emit("state_" + state)

    def post(self, kind, payload=None, generation=None):
        try:
            self.events.put_nowait((kind, payload, generation, None))
        except queue.Full:
            self.overflow.set()

    def action(self, action, timeout=12):
        result = queue.Queue(maxsize=1)
        try:
            self.events.put(("action", action, None, result), timeout=.2)
        except queue.Full:
            raise ValueError("设备繁忙，请稍后重试")
        try:
            ok, value = result.get(timeout=timeout)
        except queue.Empty:
            raise ValueError("操作超时，请查看当前状态")
        if not ok:
            raise ValueError(value)
        return value

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=20)
            if self.thread.is_alive():
                raise RuntimeError("companion shutdown timed out")

    def _run(self):
        try:
            while not self.stopping.is_set():
                if self.overflow.is_set():
                    self.overflow.clear()
                    with self.lock:
                        self.data["metrics"]["queue_overflows"] += 1
                    self._fail("处理队列已满，会话已停止；请降低负载后重新连接")
                try:
                    wait = .05
                    if self.silence_remaining:
                        wait = min(wait, max(0., self.silence_due - time.monotonic()))
                    kind, payload, generation, result = self.events.get(timeout=wait)
                except queue.Empty:
                    self._safe_tick()
                    continue
                try:
                    if generation is not None and generation != self.generation:
                        continue
                    if kind == "action":
                        self._action(payload)
                    elif kind == "message":
                        self._message(payload)
                    elif kind == "pcm":
                        if (self.data["state"] == "listening" or (self._duplex() and self.data["state"] in ("thinking", "speaking"))) and not self.data["muted"] and payload[0] == self.capture_epoch:
                            pcm = payload[1]
                            peak = max((abs(value[0]) for value in struct.iter_unpack(
                                "<h", pcm[:len(pcm) // 2 * 2])), default=0)
                            with self.lock:
                                self.data["metrics"]["capture_peak_amplitude"] = max(
                                    self.data["metrics"]["capture_peak_amplitude"], peak)
                            if self.config.get("local_asr_streaming"):
                                self.transport.send_pcm(pcm)
                            else:
                                self.transport.send(self.codec.encode(pcm))
                            self.turn_audio_frames_sent += 1
                            with self.lock:
                                self.data["metrics"]["audio_frames_sent"] += 1
                    elif kind == "error":
                        self._fail(str(payload))
                    elif kind == "face":
                        self._set(face=payload)
                    if result:
                        result.put((True, self.snapshot()))
                except ValueError as exc:
                    if result:
                        if kind == "action" and payload == "connect":
                            self._fail("语音准备失败：" + str(exc))
                        result.put((False, str(exc)))
                    else:
                        self._fail("后端协议或音频数据无效")
                except Exception:
                    self._fail("设备或网络操作失败，请检查音频设备、依赖和后端配置")
                    if result:
                        result.put((False, self.data["error"]))
                self._safe_tick()
        finally:
            self._release()
            self._set(state="offline", connected=False)

    def _safe_tick(self):
        try:
            self._tick()
        except Exception:
            self._fail("会话维护失败，已停止录放音；请重新连接")

    def _release(self):
        self.echo_guard.clear()
        self.duplex_overlap = False
        self.duplex_echo_suspected = False
        self.last_playback_at = -1000.
        self.echo_refresh_at = 0.
        self.pending_utterances.clear()
        self.response_busy = self.response_produced = False
        self._set(input_state="off", next_transcript="", pending_utterances=0, echo_suspect="", echo_suspect_id=0)
        self.auto_until = self.auto_due = 0.
        self._set(continuous=False)
        self.generation += 1
        self.accept_audio = False
        self.deadline = self.demo_due = 0
        self.silence_remaining = 0
        self.silence_due = 0.
        self.tts_ended = False
        for name in ("transport", "audio", "codec"):
            resource = getattr(self, name)
            setattr(self, name, None)
            if resource:
                try:
                    resource.close() if name != "audio" else resource.stop()
                except Exception:
                    pass
        self.session = ""

    def _fail(self, reason):
        self.diagnostics.emit("session_failed")
        self._release()
        self._set(state="error", connected=False, muted=True, error=reason, emotion="neutral", voice_progress="failed")

    def _send(self, kind, **fields):
        if self.transport:
            self.transport.send(dict(type=kind, session_id=self.session, **fields))

    def _duplex(self):
        return self.config.get("full_duplex", False) and self.data["continuous"]

    def _echo_context(self):
        return (self.duplex_overlap or
                (self.audio and self.audio.playback_busy() and getattr(self.audio, "first_write_monotonic", 0.) > 0) or
                time.monotonic() - self.last_playback_at < 3.)

    def _accept_duplex_text(self, text):
        self._set(next_transcript=text, echo_suspect="", echo_suspect_id=0)
        if self.response_busy:
            if len(self.pending_utterances) >= 3:
                self._fail("待回复的话超过三句，已停止会话；请重新开启后逐句交流")
                return
            self.pending_utterances.append(text)
            self._set(pending_utterances=len(self.pending_utterances))
        else:
            self._start_duplex_reply(text)

    def _start_duplex_reply(self, text):
        self.response_busy = True
        self.response_produced = False
        self.accept_audio = self.tts_ended = False
        self.capture_stopped_at = time.monotonic()
        self.audio_queued_at = 0.
        self.turn_audio_frames_received = 0
        if self.data["latency_ms"]:
            self._set(last_latency_ms=dict(self.data["latency_ms"]))
        self._set(state="thinking", transcript=text, transcript_partial=False, reply="",
                  latency_ms={}, voice_progress="waiting_reply", emotion="thinking")
        self.deadline = time.monotonic() + self.config["response_timeout_s"]
        self._send("reply", text=text)

    def _finish_duplex_reply(self):
        if not self.response_busy or not self.response_produced or (self.audio and self.audio.playback_busy()):
            return
        self.response_busy = self.response_produced = False
        self.tts_ended = self.accept_audio = False
        self.deadline = 0.
        if self.pending_utterances:
            text = self.pending_utterances.popleft()
            self._set(pending_utterances=len(self.pending_utterances))
            self._start_duplex_reply(text)
        else:
            self._set(state="listening", emotion="neutral",
                      voice_progress="recording" if self.data["input_state"] == "recognizing" else "waiting_speech")

    def _idle(self, progress="idle"):
        if self._duplex() and not self.data["muted"]:
            self.deadline = 0.
            self._set(state="listening", voice_progress="waiting_speech", emotion="neutral")
            return
        self.deadline = 0
        self._set(state="muted" if self.data["muted"] else "idle", emotion="neutral", voice_progress=progress)
        if self.data["continuous"] and not self.data["muted"]:
            self.auto_due = time.monotonic() + .4
        elif self.config.get("aec_enabled") and self.audio:
            self.audio.stop_capture()

    def _action(self, action):
        if action.startswith(("confirm_echo:", "dismiss_echo:")):
            command, candidate_id = action.split(":", 1)
            if not self._duplex() or self.data["muted"] or not self.data["connected"]:
                raise ValueError("请先开启连续对话")
            if candidate_id != str(self.data["echo_suspect_id"]) or not self.data["echo_suspect"]:
                raise ValueError("这条待确认内容已变化，请查看最新提示")
            text = self.data["echo_suspect"]
            self._set(echo_suspect="", echo_suspect_id=0)
            if command == "confirm_echo" and text:
                self._accept_duplex_text(text)
            return
        if action == "continuous":
            if not self.config.get("local_asr_streaming"):
                raise ValueError("当前配置不支持流式对话")
            if not self.data["connected"] or self.data["muted"] or self.data["state"] != "idle":
                raise ValueError("请先准备语音并开启麦克风，等待当前对话结束")
            self.auto_until = 0. if self.config.get("standalone_voice", False) else time.monotonic() + 15
            self.diagnostics.emit("continuous_started")
            self._set(continuous=True)
            self._action("listen")
        elif action == "keep_listening":
            if self.data["continuous"] and not self.config.get("standalone_voice", False):
                self.auto_until = time.monotonic() + 15
        elif action == "connect":
            if self.data["connected"] or self.data["state"] == "connecting":
                return
            self._release()
            self._set(state="connecting", error="", transcript="", reply="", voice_progress="idle")
            if self.config["mode"] == "demo":
                self.session = "demo"
                self._set(connected=True)
                self._idle()
                return
            if self.audio_factory is None:
                from .audio import AudioIO, OpusCodec
                from .transport import CloudTransport
                if self.config.get("voice_backend") == "local":
                    from .local_voice import LocalVoiceTransport
                    transport_class = LocalVoiceTransport
                else:
                    transport_class = CloudTransport
                self.audio_factory, self.codec_factory, self.transport_factory = AudioIO, OpusCodec, transport_class
            generation = self.generation
            self.codec = self.codec_factory()
            self.audio = self.audio_factory(self.config, on_error=lambda e: self.post(
                "error", "音频设备失败，请检查声卡与 ALSA 配置", generation))
            self.transport = self.transport_factory(self.config,
                lambda value: self.post("message", value, generation),
                lambda e: self.post("error", str(e) if self.config.get("voice_backend") == "local" else "语音连接已断开，请重新连接", generation))
            self.transport.connect()
            self.transport.send({"type": "hello", "version": 1, "transport": "websocket",
                                 "device_id": self.config["device_id"], "audio_params": AUDIO_PARAMS})
            self.deadline = time.monotonic() + self.config["network_timeout_s"] + 3
        elif action == "disconnect":
            self._release()
            self._set(state="offline", connected=False, muted=True, emotion="neutral", voice_progress="idle")
        elif action == "mute":
            self.diagnostics.emit("microphone_muted")
            self.auto_due = self.auto_until = 0.
            self._set(continuous=False)
            reconnect = self._interrupt()
            self._set(muted=True)
            if reconnect:
                self._action("connect")
            elif self.data["connected"]:
                self._idle()
        elif action == "unmute":
            self.diagnostics.emit("microphone_enabled")
            if self.data["muted"]:
                self._set(muted=False)
                if self.data["connected"]:
                    self._idle()
        elif action == "listen":
            if not self.data["connected"]:
                raise ValueError("请先连接语音服务")
            if self.data["muted"]:
                raise ValueError("麦克风已静音，请先取消静音")
            if self.data["state"] == "listening":
                return
            if self.data["state"] not in ("idle", "muted"):
                raise ValueError("请先停止当前回答，再开始新一轮对话")
            self._interrupt(keep_capture=self.config.get("aec_enabled", False))
            self.capture_epoch += 1
            epoch = self.capture_epoch
            self.turn_audio_frames_sent = 0
            self.turn_audio_frames_received = 0
            with self.lock:
                self.data["metrics"]["capture_peak_amplitude"] = 0
            self.auto_due = 0.
            self.capture_stopped_at = self.audio_queued_at = 0.
            if self.data["latency_ms"]:
                self._set(last_latency_ms=dict(self.data["latency_ms"]))
            self._set(latency_ms={})
            self._set(input_state="waiting_speech" if self._duplex() else "off", next_transcript="")
            self._set(state="listening", transcript="", transcript_partial=False, reply="", error="", emotion="neutral", voice_progress="waiting_speech" if self.data["continuous"] else "recording")
            self._send("listen", state="start", mode="auto" if self.data["continuous"] else self.config.get("listen_mode", "manual"))
            generation = self.generation
            if self.audio:
                self.audio.start(lambda pcm: self.post("pcm", (epoch, pcm), generation))
            self.deadline = 0. if self.data["continuous"] else time.monotonic() + min(self.config["max_listen_s"], 29 if self.config.get("local_asr_streaming") else 120)
        elif action == "stop":
            if self._duplex():
                self._action("mute")
                return
            if self.data["state"] != "listening":
                return
            self.capture_stopped_at = time.monotonic()
            self.capture_epoch += 1
            if self.audio:
                self.audio.pause_capture() if self.config.get("aec_enabled") else self.audio.stop_capture()
            if self.config["mode"] == "live" and self.turn_audio_frames_sent == 0:
                # Sending listen/stop with no PCM can leave the backend waiting
                # indefinitely. Fence late replies by closing this connection.
                try:
                    self._send("abort", reason="no_audio")
                except Exception:
                    pass  # Local cleanup is still required if the peer is gone.
                self._fail("未采集到音频，请检查麦克风和 ALSA 设备配置后重新连接")
                return
            if self.config["mode"] == "live" and self.config.get("listen_mode", "manual") == "realtime":
                self.silence_remaining = SILENCE_TAIL_FRAMES
                self.silence_due = time.monotonic() + SILENCE_FRAME_INTERVAL
            else:
                self._send("listen", state="stop")
            self._set(state="thinking", emotion="thinking", voice_progress="waiting_reply" if self.data["transcript"] else "waiting_recognition")
            self.deadline = time.monotonic() + self.config["response_timeout_s"]
            if self.config["mode"] == "demo":
                self._set(transcript="演示：你好，介绍一下你自己。", voice_progress="waiting_reply")
                self.demo_phase, self.demo_due = 1, time.monotonic() + .5
        elif action == "interrupt":
            self.auto_due = self.auto_until = 0.
            self._set(continuous=False)
            reconnect = self._interrupt()
            if reconnect:
                self._action("connect")
            elif self.data["connected"]:
                self._idle()
        else:
            raise ValueError("不支持的操作")

    def _interrupt(self, keep_capture=False):
        active = self.data["connected"] and self.data["state"] in ("listening", "thinking", "speaking")
        self.capture_epoch += 1
        self.accept_audio = False
        self.demo_due = self.deadline = 0
        self.silence_remaining = 0
        self.silence_due = 0.
        self.tts_ended = False
        if self.audio:
            if keep_capture and not active:
                self.audio.pause_capture()
            else:
                self.audio.stop_capture()
            self.audio.interrupt()
        if active:
            try:
                self._send("abort", reason="user")
            finally:
                if self.config["mode"] == "live":
                    # Protocol v1 has no turn IDs. A fresh connection is the fence
                    # against late replies from the interrupted turn.
                    self._release()
                    self._set(state="offline", connected=False)
            return self.config["mode"] == "live"
        return False

    def _message(self, value):
        if isinstance(value, (PcmAudio, PcmStreamChunk)):
            if self.config.get("voice_backend") != "local":
                raise ValueError("Local PCM is not accepted from an Android backend")
            value.validate()
            if self.accept_audio and not self.data["muted"] and self.audio:
                if not self.audio_queued_at:
                    self.audio_queued_at = time.monotonic()
                if isinstance(value, PcmStreamChunk):
                    self.audio.play_pcm_stream(value)
                else:
                    self.audio.play_pcm(value)
                self.turn_audio_frames_received += 1
                self._set(voice_progress="receiving_audio")
                with self.lock:
                    self.data["metrics"]["audio_frames_received"] += 1
            return
        if isinstance(value, bytes):
            if self.accept_audio and not self.data["muted"] and self.audio:
                if len(value) > 4096:
                    raise ValueError("oversized audio")
                self.audio.play(value)
                self.turn_audio_frames_received += 1
                self._set(voice_progress="receiving_audio")
                with self.lock:
                    self.data["metrics"]["audio_frames_received"] += 1
            return
        if not isinstance(value, dict):
            raise ValueError("message must be an object")
        kind = value.get("type")
        if kind == "hello":
            if self.data["state"] != "connecting":
                return
            sample_rate, session = validate_hello(value)
            self.audio.configure_output(sample_rate)
            self.session = session
            self._set(connected=True)
            self._idle()
            return
        if not self.data["connected"] or (value.get("session_id") not in (None, self.session)):
            return
        if kind == "latency" and self.config.get("voice_backend") == "local":
            values = value.get("values")
            allowed = {"asr_first_partial_ms", "asr_finalize_ms", "cloud_ms", "cloud_first_token_ms", "cloud_first_sentence_ms", "tts_first_pcm_ms", "tts_first_audio_ms", "tts_total_ms"}
            if (not isinstance(values, dict) or set(values) - allowed or any(
                    type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 240000 for v in values.values())):
                raise ValueError("Invalid stage timing")
            self._set(latency_ms=dict(self.data["latency_ms"], **values))
            return
        if kind.startswith("duplex_") and self._duplex():
            if self.data["muted"] or not self.data["connected"]:
                return
            if kind == "duplex_waiting":
                self.duplex_overlap = False
                self.duplex_echo_suspected = False
                self._set(input_state="waiting_speech")
                if self.data["state"] == "listening":
                    self._set(voice_progress="waiting_speech")
            elif kind == "duplex_started":
                self.duplex_echo_suspected = False
                self.duplex_overlap = bool(self.audio and self.audio.playback_busy() and getattr(self.audio, "first_write_monotonic", 0.) > 0) or time.monotonic() - self.last_playback_at < 3.
                self._set(input_state="recognizing", next_transcript="")
                if self.data["state"] == "listening":
                    self._set(voice_progress="recording")
            elif kind == "duplex_partial":
                text = self._text(value.get("text", ""))
                suspect = self._echo_context() and self.echo_guard.suspected(text, time.monotonic())
                # A later ASR revision must not erase evidence from this utterance.
                # Keep it reviewable through the existing confirm/dismiss path.
                self.duplex_echo_suspected |= bool(suspect)
                self._set(next_transcript="" if self.duplex_echo_suspected else text)
            elif kind == "duplex_final":
                text = self._text(value.get("text", "")).strip()
                if text:
                    if self.duplex_echo_suspected or (self._echo_context() and self.echo_guard.suspected(text, time.monotonic())):
                        with self.lock:
                            self.data["metrics"]["echo_suspicions"] += 1
                        self.diagnostics.emit("echo_quarantined")
                        self.echo_sequence += 1
                        self._set(echo_suspect=text, echo_suspect_id=self.echo_sequence, next_transcript="")
                    else:
                        self._accept_duplex_text(text)
            return
        if kind == "response_complete" and self._duplex():
            self.response_produced = True
            self._finish_duplex_reply()
            return
        if kind in ("asr_waiting", "asr_started") and self.config.get("local_asr_streaming"):
            if self.data["continuous"] and self.data["state"] == "listening" and not self.data["muted"]:
                if kind == "asr_waiting":
                    self.deadline = 0.
                    self._set(voice_progress="waiting_speech", transcript="", transcript_partial=False)
                elif self.data["voice_progress"] == "waiting_speech":
                    self.deadline = time.monotonic() + min(self.config["max_listen_s"], 29)
                    self._set(voice_progress="recording")
            return
        if kind == "asr_endpoint" and self.config.get("local_asr_streaming"):
            if self.data["state"] == "listening":
                self._action("stop")
            return
        if kind == "asr_empty" and self.config.get("local_asr_streaming"):
            if self.data["state"] == "thinking":
                self._set(transcript="", transcript_partial=False)
                self._idle("no_speech")
            return
        if kind == "stt" and self.data["state"] in ("listening", "thinking", "speaking"):
            self._set(transcript=self._text(value.get("text", "")), transcript_partial=value.get("partial") is True)
            if self.data["state"] == "thinking" and self.data["transcript"] and not self.data["reply"] and not self.data["transcript_partial"]:
                self._set(voice_progress="waiting_reply")
        elif kind == "llm" and self.data["state"] in ("thinking", "speaking"):
            emotion = value.get("emotion", "neutral")
            self._set(emotion=emotion if emotion in EMOTIONS else "neutral")
            if "text" in value:
                self._set(reply=self._text(value["text"]))
                if self.data["reply"] and self.data["state"] == "thinking":
                    self._set(voice_progress="waiting_audio")
        elif kind == "tts":
            state = value.get("state")
            if state == "start" and self.data["state"] in ("listening", "thinking") and not self.data["muted"]:
                if not self._duplex():
                    self.capture_epoch += 1
                    if self.audio:
                        self.audio.pause_capture() if self.config.get("aec_enabled") else self.audio.stop_capture()
                if self._duplex() and self.data["reply"]:
                    self.echo_guard.remember(self.data["reply"], time.monotonic())
                self.silence_remaining = 0
                self.silence_due = 0.
                self.accept_audio = True
                self.tts_ended = False
                self._set(state="speaking", voice_progress="waiting_audio")
                self.deadline = time.monotonic() + self.config["response_timeout_s"]
            elif state == "stop" and self.data["state"] == "speaking":
                self.accept_audio = False
                self.tts_ended = True
            elif state == "sentence_start" and self.data["state"] == "speaking":
                sentence = self._text(value.get("text", ""))
                if not (self.config.get("voice_backend") == "local" and
                        self.config.get("local_tts_streaming") and self.config.get("qianfan_streaming", True)):
                    self._set(reply=sentence)
                if self._duplex():
                    self.echo_guard.remember(sentence, time.monotonic())
        elif kind == "error":
            self._fail("后端返回错误，请检查服务与认证配置")
        # Remote messages can never arm or command an actuator.

    @staticmethod
    def _text(value):
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError("invalid text")
        return value

    def _tick(self):
        now = time.monotonic()
        if self._duplex() and self.audio and self.audio.playback_busy() and getattr(self.audio, "first_write_monotonic", 0.) > 0:
            self.last_playback_at = now
            if self.data["input_state"] == "recognizing":
                self.duplex_overlap = True
            if now >= self.echo_refresh_at and self.data["reply"]:
                self.echo_guard.remember(self.data["reply"], now)
                self.echo_refresh_at = now + 5.
        first_write = getattr(self.audio, "first_write_monotonic", 0.) if self.audio else 0.
        if (self.audio_queued_at and first_write >= self.audio_queued_at and
                "playback_queue_ms" not in self.data["latency_ms"]):
            timing = dict(self.data["latency_ms"], playback_queue_ms=round((first_write - self.audio_queued_at) * 1000, 1))
            if self.capture_stopped_at:
                timing["stop_to_audio_ms"] = round((first_write - self.capture_stopped_at) * 1000, 1)
            self._set(latency_ms=timing)
        if self.data["continuous"] and not self.config.get("standalone_voice", False) and now >= self.auto_until:
            self.diagnostics.emit("browser_lease_expired")
            self._action("mute")
            return
        if self.auto_due and now >= self.auto_due:
            self.auto_due = 0.
            if self.data["continuous"] and self.data["connected"] and not self.data["muted"] and self.data["state"] == "idle":
                self._action("listen")
        if self.silence_remaining and now >= self.silence_due:
            if self.data["state"] == "thinking" and self.data["connected"] and not self.data["muted"]:
                self.transport.send(self.codec.encode(SILENCE_FRAME))
                with self.lock:
                    self.data["metrics"]["audio_frames_sent"] += 1
                self.silence_remaining -= 1
                # Never burst to catch up after a delayed event-loop iteration.
                self.silence_due = time.monotonic() + SILENCE_FRAME_INTERVAL if self.silence_remaining else 0.
            else:
                self.silence_remaining = 0
                self.silence_due = 0.
        if self.demo_due and now >= self.demo_due:
            if self.demo_phase == 1:
                self._set(state="speaking", emotion="happy", voice_progress="receiving_audio",
                    reply="你好！我是设备交互助手。语音、表情与视觉状态可以在这里协同工作。当前是演示模式，没有采集或播放真实音频。")
                self.demo_phase, self.demo_due = 2, now + 2
            else:
                self.demo_due = 0
                self._idle()
        if self._duplex():
            self._finish_duplex_reply()
        if not self._duplex() and self.tts_ended and self.audio and not self.audio.playback_busy():
            self.tts_ended = False
            self._idle("complete")
        if self.deadline and now >= self.deadline:
            if self.data["state"] == "listening":
                self._action("stop")
            else:
                self._fail(self._timeout_reason())

    def _timeout_reason(self):
        if self.data["state"] == "connecting":
            return "语音握手超时，请检查后端地址和设备认证后重新连接"
        stage = self.data["voice_progress"]
        if self.config.get("voice_backend") == "local":
            detail = {"waiting_recognition": "本地语音识别超时，请检查模型与设备负载",
                      "waiting_reply": "千帆文字回答超时，请检查网络、密钥和模型权限",
                      "waiting_audio": "本地语音合成超时，请检查模型与设备负载"}.get(stage, "本地语音处理超时")
            return detail + "；已停止本轮，请重新连接"
        if stage == "waiting_recognition":
            detail = "已上传录音，但未收到识别结果；请检查录音电平和后端识别服务"
        elif stage == "waiting_reply":
            detail = "已收到识别文字，但未收到回答；请检查后端对话服务"
        elif stage == "waiting_audio":
            detail = "后端已进入回复阶段，但未收到语音音频；请检查后端语音合成服务"
        elif self.turn_audio_frames_received:
            detail = "已收到部分语音，但回复未正常结束；请检查后端连接"
        else:
            detail = "语音服务未按时完成回复，请检查后端状态"
        return detail + "。会话超时，已停止录放音；请重新连接"

def validate_hello(value):
    params = value.get("audio_params", AUDIO_PARAMS)
    if not isinstance(params, dict) or any(params.get(k, v) != v for k, v in AUDIO_PARAMS.items() if k != "sample_rate"):
        raise ValueError("unsupported audio format")
    rate = params.get("sample_rate", 16000)
    if type(rate) is not int or rate not in (16000, 24000, 48000):
        raise ValueError("unsupported playback sample rate")
    session = value.get("session_id")
    if not isinstance(session, str) or not session or len(session) > 256:
        raise ValueError("invalid session")
    return rate, session
