"""Explicit backlight I/O; no board-specific gain or actuator control."""
from pathlib import Path

from .protocol import INT_MAX, android_int, legacy_target


class LinuxBacklight:
    def __init__(self, path):
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise ValueError("backlight path must be absolute")
        self.path = Path(path)

    def _read_value(self, name):
        with (self.path / name).open("r", encoding="ascii") as stream:
            text = stream.read(65).strip()
        if not text or len(text) > 64 or not all("0" <= char <= "9" for char in text):
            raise ValueError("invalid backlight reading")
        value = int(text)
        if value > INT_MAX:
            raise ValueError("invalid backlight reading")
        return value

    def _read_state(self):
        maximum = self._read_value("max_brightness")
        raw = self._read_value("brightness")
        actual = self._read_value("actual_brightness")
        if maximum < 1 or raw > maximum or actual > maximum:
            raise ValueError("invalid backlight range")
        return {"available": True, "raw": raw, "maximum": maximum, "actual": actual,
                "verified": raw == actual}

    def _write_value(self, value):
        with (self.path / "brightness").open("w", encoding="ascii") as stream:
            if stream.write(str(value) + "\n") != len(str(value)) + 1:
                raise OSError("short backlight write")

    def snapshot(self):
        try:
            return self._read_state()
        except (OSError, ValueError, UnicodeError):
            return {"available": False, "verified": False, "error": "backlight read failed"}

    def adjust(self, command):
        android_int(command)
        try:
            before = self._read_state()
            if not before["verified"]:
                raise ValueError("unverified starting backlight")
        except (OSError, ValueError, UnicodeError):
            return {"ok": False, "status": "error", "available": False,
                    "error": "backlight initial read failed", "write_attempted": False,
                    "rollback_verified": False, "uncertain": False}
        target = legacy_target(before["raw"], 0, before["maximum"], command)
        return self._apply(before, target)

    def set_raw(self, value):
        """MQTT uses absolute raw brightness, unlike the original relative MCP."""
        android_int(value)
        try:
            before = self._read_state()
            if not before["verified"] or not 0 <= value <= before["maximum"]:
                raise ValueError("invalid absolute backlight")
        except (OSError, ValueError, UnicodeError):
            return {"ok": False, "status": "error", "available": False,
                    "error": "backlight initial read or absolute range failed", "write_attempted": False,
                    "rollback_verified": False, "uncertain": False}
        return self._apply(before, value)

    def _apply(self, before, target):
        try:
            self._write_value(target)
            after = self._read_state()
            if after["maximum"] != before["maximum"] or after["raw"] != target or after["actual"] != target:
                return self._rollback(before, "backlight verification failed")
            return dict(after, ok=True, status="applied", target=target, write_attempted=True,
                        rollback_verified=False, uncertain=False)
        except (OSError, ValueError, UnicodeError):
            return self._rollback(before, "backlight write or readback failed")

    def _rollback(self, before, error):
        try:
            self._write_value(before["raw"])
            restored = self._read_state()
            if any(restored[key] != before[key] for key in ("raw", "actual", "maximum")):
                raise ValueError("backlight restoration did not verify")
            return dict(restored, ok=False, status="error", error=error,
                        write_attempted=True, rollback_verified=True, uncertain=False)
        except (OSError, ValueError, UnicodeError):
            return {"ok": False, "status": "error", "available": False,
                    "error": error, "rollback_error": "backlight rollback failed",
                    "write_attempted": True, "rollback_verified": False, "uncertain": True}
