"""Explicit, bounded controls for the verified RV1126B mixer and backlight."""
import copy
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import threading
import time


class DeviceError(RuntimeError):
    pass


def run_bounded(argv, timeout=0.25):
    """Capture at most 8 KiB; kill/reap a stuck mixer command."""
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL)
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DeviceError("设备控制超时")
            ready, _, _ = select.select([process.stdout], [], [], remaining)
            if not ready:
                raise DeviceError("设备控制超时")
            chunk = os.read(process.stdout.fileno(), 4096)
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > 8192:
                raise DeviceError("设备控制输出异常")
        if process.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
            raise DeviceError("设备控制命令失败")
        return output.decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        raise DeviceError("设备控制超时") from exc
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


class DeviceManager:
    def __init__(self, enabled=False, runner=None,
                 backlight_path="/sys/class/backlight/backlight"):
        self.enabled = bool(enabled)
        self._runner = runner or run_bounded
        self._mixer = self.enabled and (runner is not None or bool(shutil.which("amixer")))
        self._backlight = Path(backlight_path)
        self._lock = threading.Lock()
        self._closed = False
        self._next_read = 0.
        self._data = {"available": False, "busy": False, "error": "",
                      "volume_percent": None, "speaker_enabled": None,
                      "mic_gain_db": None, "brightness_percent": None,
                      "supported": dict.fromkeys(("volume", "speaker", "mic_gain", "brightness"), False),
                      "diagnostic": "音量上限为0dB；麦克风增益仅支持0–42dB（每档6dB）；亮度最低10%；修改即时生效，未保存重启配置。"}

    def _read_control(self, name):
        text = self._runner(["amixer", "-c", "0", "sget", name])
        if len(text) > 8192:
            raise DeviceError("设备控制输出异常")
        if name in ("Speaker", "spk switch"):
            match = re.search(r"^\s*Mono: Playback \[(on|off)\]", text, re.M)
            if match:
                return int(match.group(1) == "on")
        else:
            match = re.search(r"^\s*Mono: (?:Playback )?([0-9]{1,4})\s+\[", text, re.M)
            if match:
                return int(match.group(1))
        raise DeviceError("无法读取设备控制值")

    def _write_control(self, name, value):
        setting = ("on" if value else "off") if name in ("Speaker", "spk switch") else str(value)
        self._runner(["amixer", "-c", "0", "sset", name, setting])

    def _pair(self, names, maximum):
        values = [self._read_control(name) for name in names]
        if any(value > maximum for value in values):
            raise DeviceError("设备控制值超出预期范围")
        return values

    def _brightness(self):
        with (self._backlight / "max_brightness").open() as stream:
            maximum = int(stream.read(32))
        with (self._backlight / "brightness").open() as stream:
            value = int(stream.read(32))
        if not 0 < maximum <= 65535 or not 0 <= value <= maximum:
            raise DeviceError("背光范围无效")
        return value, maximum

    def _refresh(self, clear_error=False):
        fields = {"volume": "volume_percent", "speaker": "speaker_enabled",
                  "mic_gain": "mic_gain_db", "brightness": "brightness_percent"}
        for key, field in fields.items():
            self._data[field] = None
            self._data["supported"][key] = False
        if not self.enabled or self._closed:
            self._data["available"] = False
            return
        if self._mixer:
            for key, names, maximum in (("volume", ("DACL", "DACR"), 255),
                                        ("speaker", ("Speaker", "spk switch"), 1),
                                        ("mic_gain", ("ADCL PGA", "ADCR PGA"), 14)):
                try:
                    values = self._pair(names, maximum)
                    self._data["supported"][key] = True
                    if key == "speaker":
                        value = all(values)
                    elif values[0] != values[1]:
                        value = None
                    elif key == "volume":
                        value = round(values[0] * 100 / 191) if values[0] <= 191 else None
                    else:
                        value = values[0] * 3
                    self._data[fields[key]] = value
                except (OSError, ValueError, DeviceError):
                    pass
        try:
            value, maximum = self._brightness()
            self._data["brightness_percent"] = round(value * 100 / maximum)
            self._data["supported"]["brightness"] = True
        except (OSError, ValueError, DeviceError):
            pass
        self._data["available"] = any(self._data["supported"].values())
        if clear_error:
            self._data["error"] = "" if self._data["available"] else "无法读取设备音频和背光控制"
        self._next_read = time.monotonic() + 2

    def snapshot(self):
        if not self._lock.acquire(blocking=False):
            data = copy.deepcopy(self._data)
            data["busy"] = True
            return data
        try:
            if time.monotonic() >= self._next_read:
                self._refresh(clear_error=True)
            return copy.deepcopy(self._data)
        finally:
            self._lock.release()

    def _set_pair(self, names, target, maximum):
        original = self._pair(names, maximum)
        try:
            for name in names:
                self._write_control(name, target)
            if self._pair(names, maximum) != [target, target]:
                raise DeviceError("设备未应用请求值")
        except (OSError, ValueError, DeviceError) as exc:
            restored = True
            for name, value in zip(names, original):
                try:
                    self._write_control(name, value)
                except (OSError, ValueError, DeviceError):
                    restored = False
            try:
                restored = self._pair(names, maximum) == original and restored
            except (OSError, ValueError, DeviceError):
                restored = False
            raise DeviceError("设置失败，已恢复原值" if restored else "设置失败，无法完全恢复，请检查设备") from exc

    def action(self, body):
        ranges = {"set_volume": range(101), "set_mic_gain": (0, 6, 12, 18, 24, 30, 36, 42),
                  "set_brightness": range(10, 101)}
        if not isinstance(body, dict) or set(body) != {"action", "value"}:
            raise ValueError("设备设置请求格式无效")
        operation, value = body["action"], body["value"]
        if not isinstance(operation, str) or operation not in (*ranges, "set_speaker"):
            raise ValueError("不支持的设备设置")
        if (operation == "set_speaker" and type(value) is not bool or
                operation != "set_speaker" and (type(value) is not int or value not in ranges[operation])):
            raise ValueError("设备设置值超出允许范围")
        if not self._lock.acquire(blocking=False):
            raise ValueError("设备设置正在处理中")
        try:
            if not self.enabled or self._closed:
                raise ValueError("设备设置未启用")
            self._data.update(busy=True, error="")
            try:
                if operation == "set_brightness":
                    original, maximum = self._brightness()
                    target = max(1, round(value * maximum / 100))
                    try:
                        (self._backlight / "brightness").write_text(str(target))
                        if self._brightness()[0] != target:
                            raise DeviceError("背光未应用请求值")
                    except (OSError, ValueError, DeviceError) as exc:
                        try:
                            (self._backlight / "brightness").write_text(str(original))
                            restored = self._brightness()[0] == original
                        except (OSError, ValueError, DeviceError):
                            restored = False
                        raise DeviceError("背光设置失败，已恢复原值" if restored else "背光设置失败，无法恢复原值") from exc
                elif not self._mixer:
                    raise DeviceError("未找到音频控制工具")
                elif operation == "set_volume":
                    self._set_pair(("DACL", "DACR"), round(value * 191 / 100), 255)
                elif operation == "set_speaker":
                    self._set_pair(("Speaker", "spk switch"), int(value), 1)
                else:
                    self._set_pair(("ADCL PGA", "ADCR PGA"), value // 3, 14)
            except (OSError, ValueError, DeviceError) as exc:
                self._data["error"] = str(exc) if isinstance(exc, DeviceError) else "无法访问设备控制"
            self._refresh()
            self._data["busy"] = False
            return copy.deepcopy(self._data)
        finally:
            self._data["busy"] = False
            self._lock.release()

    def close(self):
        with self._lock:
            self._closed = True
            self._refresh()
