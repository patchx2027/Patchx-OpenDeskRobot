"""PatchX product extension; inherits the bounded session/generation event gate."""
import queue
import audioop
import time
from collections import deque
from apps.companion.core import Companion
from .handsfree import BargeInGate
from .protocol import BUSINESS_TYPES
from .protocol import image_message, listen_mode_message, parse_business


class PatchXCompanion(Companion):
    def __init__(self, voice_config, business, camera_backend=None, camera_timeout_s=45.0,
                 display_service=None, motor_controller=None, handsfree=None, wake_prompt=None,
                 wake_barge_in=False, wake_barge_min_rms=1000, **factories):
        self.display = display_service
        self.motor = motor_controller
        self.audio_cleanup_uncertain = False
        self.handsfree = handsfree
        self.wake_barge_in = wake_barge_in
        self.wake_prompt = wake_prompt
        self.wake_prompt_pending = False
        self.wake_armed = handsfree is not None
        self.wake_connecting = False
        self.wake_last_rms = self.wake_peak_rms = 0
        self.standby_audio = None
        self.standby_retry_at = 0.
        self.barge_frames = deque(maxlen=12)
        self.barge_gate = BargeInGate(min_rms=wake_barge_min_rms)
        super().__init__(voice_config, **factories)
        if self.handsfree:
            self.data["capabilities"].update(wake_word=True, automatic_barge_in=wake_barge_in)
            self.data["control_mode"] = "device"
            self.data["wake_waiting"] = False
        self.business = business
        self.camera = None
        self.mqtt = None
        from .robot_state import UnifiedRobotState
        self.robot_state = UnifiedRobotState(device_id=voice_config["device_id"])
        self.camera_epoch = 0
        self.camera_request_id = 0
        self.special_listen_mode = ""
        if camera_backend is not None:
            from .camera import CameraService
            self.camera = CameraService(camera_backend, self._camera_completed, timeout_s=camera_timeout_s)

    def configure_mqtt(self, **settings):
        from .mqtt import MqttService
        self.mqtt = MqttService(on_command=self._mqtt_command, state_provider=self.robot_status, **settings)

    def _mqtt_command(self, command):
        # Preserve an independent MQTT session fence, even while voice is offline.
        if self.stopping.is_set():
            return False
        try:
            self.events.put_nowait(("message", command, None, None))
            return True
        except queue.Full:
            return False

    def robot_status(self):
        state = self.business.snapshot()
        fields = {"appVersion": "patchx-linux-0.2", "systemVersion": "Linux",
                  "camera": "ok" if state["camera"].get("status") == "sent" else "error",
                  "touchSwitch": 0 if state.get("touch_wake_enabled", False) else 1}
        for kind, maximum in (("brightness", 255), ("volume", 15)):
            value = state[kind]
            if value.get("available") and value.get("verified") and type(value.get("raw")) is int:
                fields[kind] = max(0, min(maximum, value["raw"]))
        self.robot_state.update(**fields)
        return self.robot_state.snapshot()

    def _mqtt_message(self, command):
        if self.stopping.is_set() or self.mqtt is None or not self.mqtt.accepts(command):
            return
        result = {"ok": False, "status": "unavailable", "reason": "hardware or renderer is not enabled"}
        if command.kind in ("brightness", "volume"):
            result = self.business.absolute_setting(command.kind, command.value)
        elif command.kind == "touchWakeUpSwitch":
            result = {"ok": True, "status": "configured", "physical_input_available": False}
        elif command.kind in ("divination", "decodeDreams") and self.data["connected"] and self.transport:
            action = "start" if command.flag == 1 else "end"
            mode = ("fortune" if command.kind == "divination" else "dream") if action == "start" else ""
            try:
                self.transport.send(listen_mode_message(action, mode))
                self.special_listen_mode = mode
                result = {"ok": True, "status": "sent"}
            except Exception:
                result = {"ok": False, "status": "error", "error": "mode_send_failed"}
        self.business.mqtt_result(command, result)
        if command.kind in ("test", "testEmote"):
            self._display_update(expression=True)

    def _display_update(self, expression=False):
        if self.display is not None:
            with self.lock:
                state, emotion = self.data["state"], self.data["emotion"]
            identifier = self.business.snapshot()["expression"]["id"] if expression else None
            self.display.update(state=state, emotion=emotion, expression_id=identifier)

    def _set(self, **values):
        super()._set(**values)
        if any(key in values for key in ("state", "emotion", "muted")):
            self._display_update()

    def _send(self, message_type, **fields):
        # Android suppresses ordinary listen start/stop during fortune/dream.
        if (self.special_listen_mode and message_type == "listen" and
                fields.get("state") in ("start", "stop")):
            return
        super()._send(message_type, **fields)

    def start(self):
        try:
            if self.display is not None and not self.display.start():
                raise RuntimeError("PatchX display startup failed")
            if self.camera is not None and not self.camera.start():
                raise RuntimeError("PatchX camera startup failed")
            if self.motor is not None:
                self.motor.start()
            super().start()
            self._display_update()
            if self.mqtt is not None and not self.mqtt.start():
                raise RuntimeError("PatchX MQTT startup failed")
        except Exception:
            self.close()
            raise

    def close(self):
        errors = []
        mqtt_closed = self.mqtt is None
        if self.mqtt is not None:
            try:
                mqtt_closed = self.mqtt.close()
            except Exception:
                mqtt_closed = False
        try:
            super().close()
        except Exception:
            errors.append("core")
        for name, owner in (("camera", self.camera), ("display", self.display)):
            if owner is not None:
                try:
                    if not owner.close():
                        errors.append(name)
                except Exception:
                    errors.append(name)
        if self.motor is not None:
            try:
                self.motor.close()
            except RuntimeError:
                errors.append("motor")
        if not mqtt_closed:
            errors.append("MQTT")
        if self.audio_cleanup_uncertain:
            errors.append("audio")
        if errors:
            raise RuntimeError("PatchX shutdown incomplete: " + ", ".join(errors))

    def _stop_standby(self):
        if self.standby_audio is not None:
            audio, self.standby_audio = self.standby_audio, None
            self.capture_epoch += 1
            try:
                if audio.stop() is False:
                    raise RuntimeError("standby audio cleanup is uncertain")
            except Exception:
                self.audio_cleanup_uncertain = True
                self.standby_audio = audio
                raise
            finally:
                self._set(wake_waiting=False)

    def _release(self):
        self.wake_prompt_pending = False
        self._stop_standby()
        self.barge_frames.clear()
        self.barge_gate.reset()
        self.special_listen_mode = ""
        self.camera_epoch += 1
        if self.camera is not None:
            self.camera.cancel()
        # The base service intentionally swallows audio cleanup errors. This
        # product owns a physical speaker route and must retain failed owners.
        audio = self.audio
        self.audio = None
        try:
            if audio is not None and not self.audio_cleanup_uncertain:
                try:
                    if audio.stop() is False:
                        self.audio_cleanup_uncertain = True
                except Exception:
                    self.audio_cleanup_uncertain = True
            super()._release()
        finally:
            if self.audio_cleanup_uncertain:
                self.audio = audio

    def _action(self, action):
        if action == "listen" and self.wake_prompt_pending:
            raise ValueError("请等待唤醒提示播完")
        if self.handsfree and action in ("mute", "disconnect"):
            self.wake_prompt_pending = False
            self.wake_armed = False
            self.wake_connecting = False
            self._stop_standby()
        elif self.handsfree and action == "unmute":
            self.wake_armed = True
        if action == "connect" and self.audio_cleanup_uncertain:
            raise ValueError("音频退出未完成，不能重新打开设备")
        super()._action(action)

    def _start_wake_listening(self):
        self.wake_prompt_pending = False
        self._action("unmute")
        self._set(continuous=True)
        self._action("listen")

    def _tick(self):
        super()._tick()
        if self.wake_prompt_pending and not self.audio.playback_busy():
            self._start_wake_listening()
        if (not self.wake_armed or self.audio_cleanup_uncertain or self.standby_audio is not None
                or self.data["state"] not in ("offline", "error") or time.monotonic() < self.standby_retry_at):
            return
        self.wake_connecting = False
        self.standby_retry_at = time.monotonic() + 3.
        self.handsfree.reset_wake()
        self.capture_epoch += 1
        epoch, generation = self.capture_epoch, self.generation
        self.standby_audio = self.audio_factory(self.config, on_error=lambda _: self.post(
            "error", "唤醒麦克风无法采集", generation))
        self.standby_audio.start(lambda pcm: self.post("pcm", (epoch, pcm), generation))
        self._set(wake_waiting=True)

    def _idle(self, progress="idle"):
        super()._idle(progress)
        if self.handsfree and self.auto_due:
            self.auto_due = time.monotonic() + .15

    def _keep_capture_for_tts(self):
        return bool(self.handsfree and self.data["continuous"]) or super()._keep_capture_for_tts()

    def _pcm(self, epoch, pcm):
        if epoch != self.capture_epoch:
            return
        if self.standby_audio is not None:
            self.wake_last_rms = audioop.rms(pcm, 2)
            self.wake_peak_rms = max(self.wake_peak_rms, self.wake_last_rms)
            if self.handsfree.wake(pcm):
                self.diagnostics.emit("wake_detected")
                self.wake_connecting = True
                self._action("connect")
            return
        if (self.wake_barge_in and self.data["continuous"] and self.data["state"] == "speaking"
                and not self.data["muted"]):
            self.barge_frames.append(pcm)
            if not self.audio.playback_busy():
                return
            if self.barge_gate.accept(pcm, self.handsfree.speech(pcm)):
                self.audio.interrupt()
                self.diagnostics.emit("voice_interrupt")
                self.accept_audio = self.tts_ended = False
                self._send("interrupt")
                self.deadline = 0.
                self._set(state="listening", emotion="neutral", voice_progress="recording")
                for frame in self.barge_frames:
                    super()._pcm(epoch, frame)
                self.barge_frames.clear()
            return
        super()._pcm(epoch, pcm)

    def _camera_completed(self, completion):
        self.post("message", completion, generation=completion.token[0])

    def _camera_result(self, completion):
        token = completion.token
        if (self.stopping.is_set() or not self.data["connected"] or self.transport is None or
                token[:3] != (self.generation, self.session, self.camera_epoch) or completion.cancelled):
            return
        if completion.error or completion.frame is None:
            self.business.camera_result("error", completion.query, error=completion.error or "capture_failed")
            return
        frame = completion.frame
        try:
            message = image_message(completion.query, frame.jpeg)
            # Original Android's image envelope has no session_id. Fence locally instead.
            self.transport.send(message)
        except Exception:
            self.business.camera_result("error", completion.query, error="image_send_failed")
            return
        self.business.camera_result("sent", completion.query, width=frame.width, height=frame.height,
                                    jpeg_bytes=len(frame.jpeg), captured_monotonic_ns=frame.captured_monotonic_ns)

    def snapshot(self):
        result = super().snapshot()
        result["patchx"] = self.business.snapshot()
        if self.handsfree:
            result["patchx"]["barge_in"] = self.barge_gate.snapshot()
            result["patchx"]["wake_rms"] = {"last": self.wake_last_rms, "peak": self.wake_peak_rms}
        result["patchx"]["audio_cleanup_uncertain"] = self.audio_cleanup_uncertain
        if self.motor is not None:
            result["patchx"]["motor"] = self.motor.snapshot()
        if self.camera is not None:
            result["patchx"]["camera_worker"] = self.camera.snapshot()
        if self.mqtt is not None:
            result["patchx"]["mqtt"] = self.mqtt.snapshot()
        if self.display is not None:
            display = self.display.snapshot()
            result["patchx"]["display"] = display
            expression = result["patchx"]["expression"]
            expression["renderer_available"] = display["available"]
            expression["renderer"] = display["renderer"]
            expression["unity_equivalent"] = False
            expression["rendered"] = (display["available"] and expression["id"] is not None and
                                      display["rendered_expression_id"] == expression["id"])
        audio = self.audio
        if audio is not None and callable(getattr(audio, "snapshot", None)):
            result["patchx"]["audio_worker"] = audio.snapshot()
        return result

    def _message(self, value):
        if self.mqtt is not None:
            from .mqtt import MqttCommand
            if isinstance(value, MqttCommand):
                self._mqtt_message(value)
                return
        if self.camera is not None:
            from .camera import CameraCompletion
            if isinstance(value, CameraCompletion):
                self._camera_result(value)
                return
        kind = value.get("type") if isinstance(value, dict) else None
        if isinstance(kind, str) and kind in BUSINESS_TYPES:
            # The inherited generation gate runs first; retain its voice session gate too.
            if not self.data["connected"] or value.get("session_id") not in (None, self.session):
                return
            self.business.dispatch(value)
            try:
                command = parse_business(value)
            except (ValueError, TypeError):
                return
            if command and command["kind"] == "camera" and self.camera is not None:
                self.camera_request_id += 1
                token = (self.generation, self.session, self.camera_epoch, self.camera_request_id)
                query = command["value"]
                accepted = self.camera.request(query, token)
                self.business.camera_result("queued" if accepted else "busy", query)
            if command and command["kind"] in ("expression", "greeting"):
                self._display_update(expression=True)
            return
        previous = self.data["state"]
        if (self.handsfree and self.data["continuous"] and kind == "goodbye"
                and value.get("session_id") in (None, self.session)):
            self._release()
            self._set(state="offline", connected=False, muted=True, emotion="neutral")
            return
        if (kind == "tts" and previous == "speaking" and value.get("state") == "stop"
                and value.get("session_id") in (None, self.session)):
            finish = getattr(self.audio, "finish_reply", None)
            if finish:
                finish()
        super()._message(value)
        if self.handsfree and kind == "hello" and previous == "connecting" and self.data["connected"] and self.wake_connecting:
            self.wake_connecting = False
            if self.wake_prompt:
                self.audio.play_pcm(self.wake_prompt)
                self.wake_prompt_pending = True
            else:
                self._start_wake_listening()
        if (self.wake_barge_in and kind == "tts" and previous != "speaking"
                and self.data["state"] == "speaking"):
            self.barge_frames.clear()
            self.barge_gate.reset()
            self.handsfree.reset_speech()
