"""Android business dispatcher called by the existing Companion event worker."""
import copy
import threading

from .protocol import parse_business


def unavailable(reason):
    return {"available": False, "status": "unavailable", "reason": reason}


class PatchXBusiness:
    def __init__(self, backlight=None, volume=None):
        self.backlight = backlight
        self.volume = volume
        self.lock = threading.Lock()
        self.data = {
            "processed_messages": 0, "rejected_messages": 0,
            "expression": {"id": None, "renderer_available": False, "rendered": False},
            "movement": dict(unavailable("motor adapter is not enabled"), requested_id=None, executed=False),
            "camera": unavailable("camera capture adapter is not enabled"),
            "brightness": self._initial(backlight, "backlight is not enabled"),
            "volume": self._initial(volume, "RK809 gain control has not been verified"),
            "last_source": "", "last_result": {"status": "idle", "ok": False},
        }

    @staticmethod
    def _initial(port, reason):
        if port is None:
            return unavailable(reason)
        try:
            return port.snapshot()
        except Exception:
            return {"available": False, "status": "error", "error": "device initial read failed"}

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def camera_result(self, status, query="", **metadata):
        # Only summaries enter HTTP state; never retain photo bytes or private paths.
        with self.lock:
            self.data["camera"] = dict(available=True, status=status, query=query, **metadata)
            self.data["last_result"] = {"ok": status == "sent", "status": status}

    def absolute_setting(self, kind, value):
        port = self.backlight if kind == "brightness" else self.volume
        if port is None or not hasattr(port, "set_raw"):
            result = dict(unavailable("absolute device control is not enabled"), ok=False)
        else:
            try:
                result = port.set_raw(value)
            except Exception:
                result = {"ok": False, "status": "error", "available": False,
                          "error": "absolute device setting failed", "uncertain": True}
        with self.lock:
            self.data[kind] = copy.deepcopy(result)
        return result

    def mqtt_result(self, command, result):
        with self.lock:
            self.data["mqtt_command"] = {"kind": command.kind, "value": command.value, "flag": command.flag,
                                         "intents": command.intents, "result": copy.deepcopy(result)}
            if command.kind == "touchWakeUpSwitch":
                self.data["touch_wake_enabled"] = command.value == 0
            elif command.kind in ("test", "testEmote"):
                self.data["expression"]["id"] = command.value

    def dispatch(self, message):
        try:
            command = parse_business(message)
        except (ValueError, TypeError):
            with self.lock:
                self.data["rejected_messages"] += 1
                self.data["last_result"] = {"ok": False, "status": "rejected", "error": "invalid PatchX business message"}
            return True
        if command is None:
            return False
        kind, value, source = command["kind"], command["value"], command["source"]
        with self.lock:
            self.data["processed_messages"] += 1
            self.data["last_source"] = source
            if kind in ("brightness", "volume"):
                port = self.backlight if kind == "brightness" else self.volume
                if port is None:
                    result = dict(self.data[kind], ok=False)
                else:
                    try:
                        result = port.adjust(value)
                    except Exception:
                        result = {"ok": False, "status": "error", "available": False,
                                  "error": "device adjustment failed", "uncertain": True}
                    self.data[kind] = copy.deepcopy(result)
            elif kind in ("expression", "greeting"):
                self.data["expression"]["id"] = value
                result = {"ok": False, "status": "unavailable", "reason": "Unity expression renderer is absent"}
                if kind == "greeting":
                    self.data["movement"]["requested_id"] = value
            elif kind == "movement":
                self.data["movement"]["requested_id"] = value
                result = dict(self.data["movement"], ok=False)
            elif kind == "camera":
                self.data["camera"]["query"] = value
                result = dict(self.data["camera"], ok=False)
            elif kind == "emoji_unmapped":
                result = {"ok": False, "status": "unavailable", "reason": "numeric expression mapping is absent"}
            else:
                result = {"ok": False, "status": "unavailable", "reason": "unsupported PatchX method"}
            self.data["last_result"] = result
        return True
