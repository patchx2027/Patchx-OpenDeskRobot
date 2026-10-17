"""Strict product settings; voice credentials remain environment-only."""
import ipaddress
import json
import math
import os
import re

from apps.companion.config import validate

DEFAULTS = {"client_ip": "", "backlight_enabled": False, "backlight_path": "",
            "display_enabled": False, "display_device": "/dev/fb0",
            "audio_enabled": False, "volume_initial": 8,
            "camera_enabled": False, "camera_program": "", "camera_work_dir": "",
            "camera_timeout_s": 45.0, "mqtt_enabled": False, "mqtt_broker": "",
            "mqtt_port": 8883, "mqtt_tls": True, "mqtt_username_env": "PATCHX_MQTT_USERNAME",
            "mqtt_password_env": "PATCHX_MQTT_PASSWORD", "mqtt_client_id_env": "PATCHX_MQTT_CLIENT_ID"}


def validate_product(values):
    if not isinstance(values, dict) or set(values) - {"voice", "patchx"}:
        raise ValueError("invalid product configuration keys")
    voice_values = values.get("voice", {})
    settings = values.get("patchx", {})
    if not isinstance(voice_values, dict) or not isinstance(settings, dict) or set(settings) - set(DEFAULTS):
        raise ValueError("invalid product configuration")
    # Original Android does not require peer pongs while processing speech.
    voice = validate({"websocket_heartbeat": False, **voice_values})
    product = dict(DEFAULTS, **settings)
    for key in ("backlight_enabled", "display_enabled", "audio_enabled", "camera_enabled", "mqtt_enabled", "mqtt_tls"):
        if type(product[key]) is not bool:
            raise ValueError(key + " must be boolean")
    for key in ("client_ip", "backlight_path", "display_device", "camera_program", "camera_work_dir"):
        value = product[key]
        if not isinstance(value, str) or len(value) > 2048 or any(c in value for c in "\r\n\x00"):
            raise ValueError("invalid " + key)
    if product["client_ip"]:
        try:
            ipaddress.ip_address(product["client_ip"])
        except ValueError:
            raise ValueError("invalid client_ip") from None
    for key in ("backlight_path", "display_device", "camera_program", "camera_work_dir"):
        if product[key] and not os.path.isabs(product[key]):
            raise ValueError(key + " must be absolute")
    if product["backlight_enabled"] and (voice["mode"] != "live" or not product["backlight_path"]):
        raise ValueError("backlight requires live mode and an explicit absolute path")
    if product["display_enabled"] and (voice["mode"] != "live" or not product["display_device"]):
        raise ValueError("display requires live mode and an explicit absolute device")
    if type(product["volume_initial"]) is not int or not 0 <= product["volume_initial"] <= 15:
        raise ValueError("volume_initial must be 0..15")
    if product["audio_enabled"] and (voice["mode"] != "live" or voice.get("aec_enabled")):
        raise ValueError("board audio requires live mode and the basic route without AEC")
    timeout = product["camera_timeout_s"]
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 45:
        raise ValueError("invalid camera_timeout_s")
    if product["camera_enabled"] and (voice["mode"] != "live" or
            not product["camera_program"] or not product["camera_work_dir"]):
        raise ValueError("camera requires live mode and explicit absolute paths")
    if type(product["mqtt_port"]) is not int or not 1 <= product["mqtt_port"] <= 65535:
        raise ValueError("invalid MQTT port")
    broker = product["mqtt_broker"]
    if (type(broker) is not str or len(broker) > 253 or
            any(c.isspace() for c in broker) or "\x00" in broker):
        raise ValueError("invalid MQTT broker")
    for key in ("mqtt_username_env", "mqtt_password_env", "mqtt_client_id_env"):
        if type(product[key]) is not str or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", product[key]):
            raise ValueError("invalid MQTT environment name")
    if product["mqtt_enabled"] and (voice["mode"] != "live" or not broker):
        raise ValueError("MQTT requires live mode and an explicit broker")
    if voice["mode"] == "live":
        if voice["voice_backend"] != "android":
            raise ValueError("PatchX product currently requires the Android-compatible cloud backend")
        for key in ("device_id", "client_id"):
            if key not in voice_values or not voice_values[key].strip():
                raise ValueError("live mode requires explicit " + key)
        if not product["client_ip"]:
            raise ValueError("live mode requires explicit client_ip")
    return {"voice": voice, "patchx": product}


def load_product(path=None):
    if path is None:
        return validate_product({})
    with open(path, encoding="utf-8") as stream:
        return validate_product(json.load(stream))
