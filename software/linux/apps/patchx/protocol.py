"""Bounded PatchX formats extracted from Android WebSocketManager."""
import base64
import copy
import json

from apps.companion.transport import TEXT_LIMIT

INT_MIN = -(2 ** 31)
INT_MAX = 2 ** 31 - 1
BUSINESS_TYPES = frozenset(("mcp", "welcome", "goodbye", "emoji"))
METHODS = {
    "set_volume": ("volume", "volume"),
    "adjust_volume": ("volume", "adjustment"),
    "set_brightness": ("brightness", "brightness"),
    "adjust_brightness": ("brightness", "adjustment"),
    "expression_control": ("expression", "expression_type"),
    "action_control": ("movement", "action_type"),
    "vision_recognition": ("camera", "query"),
    "secondary_vision_capture": ("camera", "query"),
}


def android_int(value):
    if type(value) is not int or not INT_MIN <= value <= INT_MAX:
        raise ValueError("expected a signed Android integer")
    return value


def legacy_target(current, minimum, maximum, command):
    """Both set_* and adjust_* use this original relative calculation."""
    current, minimum, maximum, command = map(android_int, (current, minimum, maximum, command))
    minimum, maximum = sorted((minimum, maximum))
    if minimum < 0:
        raise ValueError("device range must be nonnegative")
    if command == 0:
        return minimum
    if command == 100:
        return maximum
    percent = max(-100, min(100, command))
    # Integer half-up matches positive Java Math.round, avoiding Python ties-to-even.
    delta = (maximum * abs(percent) + 50) // 100
    target = current + delta if percent >= 0 else current - delta
    return max(minimum, min(maximum, target))


def _text(value, limit=4096):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("invalid text")
    return value


def parse_business(message):
    if not isinstance(message, dict):
        return None
    message_type = message.get("type")
    if not isinstance(message_type, str) or message_type not in BUSINESS_TYPES:
        return None
    if message_type in ("welcome", "goodbye"):
        return {"kind": "greeting", "value": 5 if message_type == "welcome" else 10,
                "source": message_type}
    if message_type == "emoji":
        mapping = _text(message.get("emoji_map"), 64).strip()
        if mapping and all("0" <= char <= "9" for char in mapping):
            return {"kind": "greeting", "value": android_int(int(mapping)), "source": "emoji"}
        return {"kind": "emoji_unmapped", "value": mapping, "source": "emoji"}
    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("invalid MCP payload")
    method, params = payload.get("method"), payload.get("params")
    if not isinstance(method, str) or len(method) > 64 or not isinstance(params, dict):
        raise ValueError("invalid MCP method or parameters")
    if method not in METHODS:
        return {"kind": "unsupported", "value": None, "source": method}
    kind, argument = METHODS[method]
    if argument not in params:
        raise ValueError("missing MCP parameter")
    value = _text(params[argument]) if kind == "camera" else android_int(params[argument])
    return {"kind": kind, "value": value, "source": method}


def enrich_hello(message, client_ip, trace_id):
    result = copy.deepcopy(message)
    result["client_ip"] = _text(client_ip, 64)
    result["trace_id"] = _text(trace_id, 128)
    return result


def _bounded(message):
    payload = json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(payload) > TEXT_LIMIT:
        raise ValueError("outgoing PatchX text exceeds transport limit")
    return message


def image_message(query, jpeg):
    _text(query)
    if not isinstance(jpeg, bytes) or not jpeg or len(jpeg) > TEXT_LIMIT:
        raise ValueError("invalid image message")
    return _bounded({"type": "image", "text": query, "image_data": {
        "data": base64.b64encode(jpeg).decode("ascii"), "format": "jpeg", "url": ""}})


def jpeg_budget(query):
    """Largest byte count whose base64 fits the actual UTF-8 JSON envelope."""
    _text(query)
    empty = {"type": "image", "text": query,
             "image_data": {"data": "", "format": "jpeg", "url": ""}}
    overhead = len(json.dumps(empty, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    return 3 * ((TEXT_LIMIT - overhead) // 4)


def user_id_message(user_id):
    if not isinstance(user_id, str):
        raise ValueError("user_id must be text")
    return _bounded({"type": "updateUserId", "state": "update", "user_id": user_id})


def listen_mode_message(action, session_type):
    if (action, session_type) not in (("start", "fortune"), ("start", "dream"), ("end", "")):
        raise ValueError("invalid special listen mode")
    return {"type": "listen_mode", "action": action, "session_type": session_type}
