"""Bounded copy of Android UnifiedRobotState's public JSON contract.

The Android getters/setters use raw 0..255 brightness and 0..15 volume,
despite their older 0..100 comments. Hardware results must be supplied by
the product; defaults do not claim the Android hard-coded 72% battery.
"""
import json
import threading


INTEGER_FIELDS = {"batteryPct": (0, 100), "touchSwitch": (0, 1),
                  "brightness": (0, 255), "volume": (0, 15),
                  "networkSignal": (0, 5), "wifiSignal": (0, 5)}
TEXT_FIELDS = frozenset(("appVersion", "firmwareVersion", "systemVersion", "camera",
                         "soundCard", "head", "direction", "leftArm", "rightArm",
                         "cpuTemperature", "ipInfo"))
PAYLOAD_FIELDS = frozenset(INTEGER_FIELDS) | TEXT_FIELDS | {"touch"}


def bounded_text(value, limit=256):
    if type(value) is not str or len(value) > limit or "\x00" in value:
        raise ValueError("invalid state text")
    try:
        if len(value.encode("utf-8")) > limit * 4:
            raise ValueError("invalid state text")
    except UnicodeError:
        raise ValueError("invalid state text") from None
    return value


def _field(name, value):
    if name in INTEGER_FIELDS:
        low, high = INTEGER_FIELDS[name]
        if type(value) is not int or not low <= value <= high:
            raise ValueError("invalid state integer")
    elif name == "touch":
        if type(value) is not bool:
            raise ValueError("invalid state touch flag")
    elif name in TEXT_FIELDS:
        bounded_text(value)
    else:
        raise ValueError("unknown state field")
    return value


def validate_status(value):
    """Return an independent, complete original-schema JSON object."""
    if type(value) is not dict or set(value) != {"userId", "sn", "type", "payload"}:
        raise ValueError("invalid status object")
    payload = value["payload"]
    if type(payload) is not dict or set(payload) != PAYLOAD_FIELDS:
        raise ValueError("invalid status payload")
    return {"userId": bounded_text(value["userId"]),
            "sn": bounded_text(value["sn"], 128),
            "type": bounded_text(value["type"], 64),
            "payload": {key: _field(key, item) for key, item in payload.items()}}


class UnifiedRobotState:
    def __init__(self, user_id="", device_id=""):
        self.lock = threading.Lock()
        self.data = {"userId": bounded_text(user_id), "sn": bounded_text(device_id, 128),
                     "type": "status", "payload": {
                         "batteryPct": 0, "touch": False, "touchSwitch": 0,
                         "brightness": 0, "volume": 0, "networkSignal": 0,
                         "appVersion": "", "firmwareVersion": "", "systemVersion": "",
                         "camera": "error", "soundCard": "error", "wifiSignal": 0,
                         "head": "", "direction": "unknown", "leftArm": "unknown",
                         "rightArm": "unknown", "cpuTemperature": "unknown", "ipInfo": ""}}

    def update(self, **fields):
        validated = {name: _field(name, value) for name, value in fields.items()}
        with self.lock:
            self.data["payload"].update(validated)
        return self

    def init_identity(self, user_id, device_id):
        user_id, device_id = bounded_text(user_id), bounded_text(device_id, 128)
        with self.lock:
            self.data["userId"], self.data["sn"] = user_id, device_id
        return self

    def set_type(self, kind):
        kind = bounded_text(kind, 64)
        with self.lock:
            self.data["type"] = kind
        return self

    def snapshot(self):
        with self.lock:
            return validate_status(self.data)

    def to_json(self):
        return json.dumps(self.snapshot(), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
