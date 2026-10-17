"""Original Android business formats; no network or device I/O."""
import importlib
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
try:
    protocol = importlib.import_module("apps.patchx.protocol")
    config = importlib.import_module("apps.patchx.config")
except ModuleNotFoundError:
    protocol = config = None


def mcp(method, **params):
    return {"type": "mcp", "payload": {"method": method, "params": params}}


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(protocol, "PatchX business protocol has not been migrated")

    def test_java_half_up_raw_maximum(self):
        self.assertEqual(protocol.legacy_target(0, 0, 255, 30), 77)
        self.assertEqual(protocol.legacy_target(100, 0, 255, -30), 23)

    def test_zero_and_hundred_are_absolute_endpoints(self):
        self.assertEqual(protocol.legacy_target(200, 0, 255, 0), 0)
        self.assertEqual(protocol.legacy_target(0, 0, 255, 100), 255)
        self.assertEqual(protocol.legacy_target(5, 10, 0, 0), 0)
        self.assertEqual(protocol.legacy_target(5, 10, 0, 100), 10)

    def test_relative_clamp_and_rounded_zero_preserved(self):
        self.assertEqual(protocol.legacy_target(1, 0, 2, 1), 1)
        self.assertEqual(protocol.legacy_target(200, 0, 255, 300), 255)
        self.assertEqual(protocol.legacy_target(200, 0, 255, -300), 0)

    def test_not_android_integer_rejected(self):
        for value in (True, 1.0, "1", None, 2 ** 31, -(2 ** 31) - 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                protocol.legacy_target(20, 0, 255, value)

    def test_set_and_adjust_keep_original_parameter_names(self):
        for method, field, kind in (("set_brightness", "brightness", "brightness"),
                                    ("adjust_brightness", "adjustment", "brightness"),
                                    ("set_volume", "volume", "volume"),
                                    ("adjust_volume", "adjustment", "volume")):
            value = protocol.parse_business(mcp(method, **{field: -30}))
            self.assertEqual(value, {"kind": kind, "value": -30, "source": method})

    def test_missing_and_wrong_nested_arguments_rejected(self):
        for message in (mcp("set_brightness"), mcp("set_brightness", brightness=True),
                        {"type": "mcp", "payload": []},
                        {"type": "mcp", "payload": {"method": [], "params": {}}},
                        mcp("expression_control", expression_type=2 ** 50)):
            with self.subTest(message=message), self.assertRaises(ValueError):
                protocol.parse_business(message)

    def test_raw_expression_action_and_camera_schema(self):
        self.assertEqual(protocol.parse_business(mcp("expression_control", expression_type=18))["value"], 18)
        self.assertEqual(protocol.parse_business(mcp("action_control", action_type=25))["kind"], "movement")
        self.assertEqual(protocol.parse_business(mcp("vision_recognition", query="看到了什么"))["value"], "看到了什么")
        self.assertEqual(protocol.parse_business(mcp("secondary_vision_capture", query="再次拍摄"))["kind"], "camera")

    def test_welcome_goodbye_and_numeric_emoji_intent(self):
        self.assertEqual(protocol.parse_business({"type": "welcome"})["value"], 5)
        self.assertEqual(protocol.parse_business({"type": "goodbye"})["value"], 10)
        self.assertEqual(protocol.parse_business({"type": "emoji", "emoji_map": " 18 "})["value"], 18)
        result = protocol.parse_business({"type": "emoji", "emoji_map": "happy"})
        self.assertEqual(result["kind"], "emoji_unmapped")
        self.assertIsNone(protocol.parse_business({"type": "tts", "state": "start"}))

    def test_enriched_hello_does_not_mutate_input(self):
        original = {"type": "hello", "device_id": "test-device", "audio_params": {"format": "opus"}}
        result = protocol.enrich_hello(original, "192.0.2.8", "test-trace")
        self.assertEqual(result["client_ip"], "192.0.2.8")
        self.assertEqual(result["trace_id"], "test-trace")
        self.assertNotIn("trace_id", original)
        self.assertEqual(result["device_id"], original["device_id"])

    def test_image_and_user_id_match_android_wire_format(self):
        self.assertEqual(protocol.image_message("看看", b"jpeg"), {
            "type": "image", "text": "看看", "image_data": {"data": "anBlZw==", "format": "jpeg", "url": ""}})
        self.assertEqual(protocol.user_id_message("123"), {"type": "updateUserId", "state": "update", "user_id": "123"})

    def test_encoded_utf8_limit_including_base64_and_escape_overhead(self):
        for builder in (lambda: protocol.image_message("q", b"x" * 49152),
                        lambda: protocol.user_id_message("汉" * 22000),
                        lambda: protocol.user_id_message("\x00" * 11000)):
            with self.assertRaises(ValueError):
                builder()
        message = protocol.image_message("q", b"x" * 48000)
        self.assertLessEqual(len(json.dumps(message, ensure_ascii=False, allow_nan=False).encode()), 65536)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(config, "PatchX product configuration has not been migrated")

    def test_default_demo_has_no_hardware_write_port(self):
        values = config.validate_product({})
        self.assertEqual(values["voice"]["mode"], "demo")
        self.assertFalse(values["patchx"]["backlight_enabled"])

    def test_patchx_omits_active_heartbeat_by_default_in_demo_and_live(self):
        for values in ({}, {"voice": {"mode": "live", "backend_url": "wss://example.test",
                                       "device_id": "test-device", "client_id": "test-client"},
                           "patchx": {"client_ip": "192.0.2.8"}}):
            with self.subTest(mode=values.get("voice", {}).get("mode", "demo")):
                result = config.validate_product(values)
                self.assertIs(result["voice"].get("websocket_heartbeat"), False)
                self.assertNotIn("websocket_heartbeat", values.get("voice", {}))

    def test_patchx_preserves_explicit_heartbeat_boolean_override(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                try:
                    result = config.validate_product({"voice": {"websocket_heartbeat": enabled}})
                except ValueError as error:
                    self.fail("Valid product heartbeat boolean was rejected: " + str(error))
                self.assertIs(result["voice"]["websocket_heartbeat"], enabled)

    def test_patchx_heartbeat_rejects_non_boolean_values(self):
        for value in (0, 1, "false", None, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                config.validate_product({"voice": {"websocket_heartbeat": value}})

    def test_live_requires_explicit_identities_and_client_ip(self):
        voice = {"mode": "live", "backend_url": "wss://example.test"}
        for fields in ({}, {"device_id": "test-device"}, {"device_id": "test-device", "client_id": "test-client"}):
            with self.assertRaises(ValueError) as raised:
                config.validate_product({"voice": dict(voice, **fields)})
            self.assertNotIn("test-device", str(raised.exception))
        values = config.validate_product({"voice": dict(voice, device_id="test-device", client_id="test-client"),
                                          "patchx": {"client_ip": "192.0.2.8"}})
        self.assertEqual(values["voice"]["device_id"], "test-device")

    def test_strict_product_keys_types_and_paths(self):
        for values in ({"token": "private"}, {"patchx": {"unknown": 1}},
                       {"patchx": {"backlight_enabled": 1}}, {"patchx": {"backlight_path": "relative"}},
                       {"voice": {"unknown": 1}}, {"patchx": {"client_ip": "not-an-ip"}},
                       {"voice": {"mode": "demo"}, "patchx": {"backlight_enabled": True}},
                       {"patchx": {"client_ip": True}}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                config.validate_product(values)


if __name__ == "__main__":
    unittest.main()
