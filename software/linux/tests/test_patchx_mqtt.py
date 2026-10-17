"""MQTT3.1.1 loopback fixture exercises the real, pinned Paho client."""
import json
import os
from pathlib import Path
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import warnings
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from apps.patchx.mqtt import MqttService, MqttCommand, parse_command
    from apps.patchx.robot_state import UnifiedRobotState
except ImportError:
    MqttService = MqttCommand = UnifiedRobotState = parse_command = None


def wait_for(predicate, timeout=3):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("MQTT loopback condition timed out")


def encoded_string(text):
    data = text.encode()
    return struct.pack("!H", len(data)) + data


def frame(header, body):
    size = len(body)
    remaining = bytearray()
    while True:
        byte = size % 128
        size //= 128
        remaining.append(byte | (128 if size else 0))
        if not size:
            return bytes([header]) + remaining + body


class LoopbackBroker:
    """Finite real TCP MQTT broker fixture, not a replacement Paho API."""
    def __init__(self, suback=1, puback=True, tls_context=None, stall_tls=False):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(4)
        self.listener.settimeout(.1)
        self.port = self.listener.getsockname()[1]
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.peers = []
        self.threads = []
        self.events = []
        self.suback = suback
        self.puback = puback
        self.tls_context = tls_context
        self.stall_tls = stall_tls
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def _accept(self):
        while not self.stopping.is_set():
            try:
                peer, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            peer.settimeout(.1)
            self._record(kind="tcp_accept")
            if self.stall_tls:
                self.stopping.wait(3)
                peer.close()
                continue
            if self.tls_context is not None:
                try:
                    peer.settimeout(1)
                    peer = self.tls_context.wrap_socket(peer, server_side=True)
                    peer.settimeout(.1)
                except (OSError, TimeoutError):
                    peer.close()
                    self._record(kind="tls_failure")
                    continue
            with self.lock:
                self.peers.append(peer)
            worker = threading.Thread(target=self._serve, args=(peer,), daemon=True)
            self.threads.append(worker)
            worker.start()

    def _exact(self, peer, size):
        result = bytearray()
        while len(result) < size and not self.stopping.is_set():
            try:
                data = peer.recv(size - len(result))
            except socket.timeout:
                continue
            if not data:
                raise EOFError()
            result.extend(data)
        if len(result) != size:
            raise EOFError()
        return bytes(result)

    def _record(self, **event):
        with self.lock:
            self.events.append(event)

    def _serve(self, peer):
        try:
            while not self.stopping.is_set():
                header = self._exact(peer, 1)[0]
                size, scale = 0, 1
                for _ in range(4):
                    byte = self._exact(peer, 1)[0]
                    size += (byte & 127) * scale
                    if not byte & 128:
                        break
                    scale *= 128
                if size > 65536:
                    raise ValueError("fixture packet limit")
                body = self._exact(peer, size)
                kind = header >> 4
                if kind == 1:
                    # Credentials are synthetic and never recorded by the fixture.
                    protocol_size = struct.unpack("!H", body[:2])[0]
                    offset = 2 + protocol_size
                    flags = body[offset + 1]
                    pos = offset + 4
                    name_size = struct.unpack("!H", body[pos:pos + 2])[0]
                    client_id = body[pos + 2:pos + 2 + name_size].decode()
                    self._record(kind="connect", flags=flags, client_id=client_id)
                    peer.sendall(b"\x20\x02\x00\x00")
                elif kind == 8:
                    mid = body[:2]
                    pos, topics = 2, []
                    while pos < len(body):
                        length = struct.unpack("!H", body[pos:pos + 2])[0]
                        topic = body[pos + 2:pos + 2 + length].decode()
                        qos = body[pos + 2 + length]
                        topics.append((topic, qos))
                        pos += length + 3
                    self._record(kind="subscribe", topics=topics)
                    peer.sendall(frame(0x90, mid + bytes([self.suback] * len(topics))))
                elif kind == 3:
                    length = struct.unpack("!H", body[:2])[0]
                    topic = body[2:2 + length].decode()
                    qos = (header >> 1) & 3
                    pos = length + 2
                    if qos:
                        mid = body[pos:pos + 2]
                        pos += 2
                        if self.puback:
                            peer.sendall(frame(0x40, mid))
                    self._record(kind="publish", topic=topic, qos=qos,
                                 retain=bool(header & 1), payload=body[pos:])
                elif kind == 4:
                    self._record(kind="puback")
                elif kind == 12:
                    peer.sendall(b"\xd0\x00")
                elif kind == 14:
                    self._record(kind="disconnect")
                    return
        except (EOFError, OSError, ValueError):
            pass
        finally:
            with self.lock:
                if peer in self.peers:
                    self.peers.remove(peer)
            peer.close()
            self._record(kind="peer_closed")

    def select(self, kind):
        with self.lock:
            return [event.copy() for event in self.events if event["kind"] == kind]

    def send(self, topic, value, header=0x32):
        payload = value if isinstance(value, bytes) else json.dumps(value).encode()
        with self.lock:
            peer = self.peers[-1]
        peer.sendall(frame(header, encoded_string(topic) + b"\x00\x73" + payload))

    def declared_oversize(self):
        with self.lock:
            peer = self.peers[-1]
        size, data = 100000000, bytearray([0x30])
        while size:
            byte, size = size % 128, size // 128
            data.append(byte | (128 if size else 0))
        peer.sendall(data)

    def drop(self):
        with self.lock:
            peer = self.peers[-1]
        peer.shutdown(socket.SHUT_RDWR)
        peer.close()

    def close(self):
        self.stopping.set()
        self.listener.close()
        with self.lock:
            peers = list(self.peers)
        for peer in peers:
            try:
                peer.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            peer.close()
        self.thread.join(1)
        for worker in self.threads:
            worker.join(1)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(parse_command, "MQTT typed parser missing")

    def test_absolute_raw_values_and_original_intents(self):
        for kind, value in (("brightness", 255), ("volume", 15), ("test", 5),
                            ("testEmote", 18), ("touchWakeUpSwitch", 0)):
            command = parse_command(json.dumps({"type": kind, "payload": {"value": value}}).encode())
            self.assertEqual((command.kind, command.value), (kind, value))
            self.assertIsInstance(command, MqttCommand)
        touch = parse_command(b'{"type":"touchWakeUpSwitch","payload":{"value":0}}')
        self.assertEqual(touch.intents, (("touchWakeUpSwitch", True),))
        display = parse_command(b'{"type":"display","payload":{"value":90}}')
        self.assertEqual(display.intents, (("display", 90), ("lcdback", 90), ("backenable", False)))
        lcdback = parse_command(b'{"type":"lcdback","payload":{"value":100}}')
        self.assertEqual(lcdback.intents, (("lcdback", 100), ("backenable", False)))

    def test_intents_and_commented_upgrades_remain_explicit(self):
        for kind, mode in (("divination", "fortune"), ("decodeDreams", "dream")):
            cmd = parse_command(json.dumps({"type": kind, "payload": {"flag": 1}}).encode())
            self.assertEqual(cmd.intents, (("listen_mode", ("start", mode)),))
        self.assertEqual(parse_command(b'{"type":"unbind","payload":{}}').intents, (("unbind", True),))
        for kind in ("appUpdate", "firmwareUpdate", "systemUpdate", "factoryReset"):
            cmd = parse_command(json.dumps({"type": kind, "payload": {}}).encode())
            self.assertFalse(cmd.supported)

    def test_strict_json_types_ranges_duplicates_and_private_fields(self):
        cases = [b'[]', b'{"type":"brightness","payload":{"value":true}}',
                 b'{"type":"volume","payload":{"value":16}}',
                 b'{"type":"brightness","payload":{"value":-1}}',
                 b'{"type":"brightness","payload":{"value":256}}',
                 b'{"type":"test","payload":{"value":5},"generation":1}',
                 b'{"type":"test","type":"volume","payload":{"value":5}}',
                 b'{"type":"divination","payload":{"flag":"1"}}',
                 b'{"type":"brightness","payload":{"value":30.0}}',
                 b'{"type":"test","payload":{"value":NaN}}',
                 b'{"type":"test","payload":{"value":5,"unexpected":[]}}',
                 b'{"type":"unknown","payload":{}}', b'\xff', b' ' * 17000]
        for payload in cases:
            with self.subTest(payload=payload[:90]), self.assertRaises(ValueError):
                parse_command(payload)

    def test_state_schema_and_unknown_hardware_do_not_claim_success(self):
        self.assertIsNotNone(UnifiedRobotState)
        state = UnifiedRobotState(user_id="fixture-user", device_id="fixture-device")
        view = state.snapshot()
        self.assertEqual((view["type"], view["sn"], view["userId"]), ("status", "fixture-device", "fixture-user"))
        self.assertEqual(view["payload"]["camera"], "error")
        self.assertEqual(view["payload"]["batteryPct"], 0)
        state.update(brightness=255, volume=15, touch=True, camera="ok")
        self.assertEqual(json.loads(state.to_json())["payload"]["brightness"], 255)
        for change in ({"brightness": 256}, {"volume": True}, {"head": "x" * 257}, {"unknown": 0}):
            with self.assertRaises(ValueError):
                state.update(**change)
        with self.assertRaises(ValueError):
            state.update(brightness=20, volume=30)
        self.assertEqual(state.snapshot()["payload"]["brightness"], 255)
        detached = state.snapshot()
        detached["payload"]["brightness"] = 0
        self.assertEqual(state.snapshot()["payload"]["brightness"], 255)

    def test_service_defaults_and_invalid_configuration(self):
        config = dict(broker="127.0.0.1", port=1883, tls=False, device_id="fixture-device",
                      env_names={"username": "FIXTURE_USER", "password": "FIXTURE_PASS"},
                      on_command=lambda command: None, state_provider=lambda: None)
        service = MqttService(**config)
        self.assertEqual((service.status_interval, service.mood_interval), (5.0, 3600.0))
        for fields in ({"port": True}, {"port": 0}, {"tls": "false"}, {"device_id": "bad/#"},
                       {"env_names": {"username": "u", "password": "secret-literal"}},
                       {"status_interval": float("nan")}, {"tls": True, "tls_context": ssl._create_unverified_context()}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                MqttService(**dict(config, **fields))


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(MqttService, "MQTT service missing")
        self.env = patch.dict(os.environ, {"FIXTURE_MQTT_USER": "fixture-user", "FIXTURE_MQTT_PASS": "synthetic-password"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.broker = LoopbackBroker()
        self.addCleanup(self.broker.close)
        self.commands = []
        self.state = UnifiedRobotState(user_id="fixture-user", device_id="fixture-device")
        self.service = MqttService(broker="127.0.0.1", port=self.broker.port, tls=False,
                                   device_id="fixture-device", env_names={"username": "FIXTURE_MQTT_USER", "password": "FIXTURE_MQTT_PASS"},
                                   on_command=self.commands.append, state_provider=self.state.snapshot,
                                   status_interval=.1, mood_interval=.25, reconnect_interval=.05)
        self.addCleanup(self.service.close)

    def connected(self):
        self.service.start()
        wait_for(lambda: self.service.snapshot()["connected"])

    def test_actual_paho_subscriptions_status_and_mood_wire_contract(self):
        self.connected()
        wait_for(lambda: len(self.broker.select("publish")) >= 4)
        subscriptions = self.broker.select("subscribe")[0]["topics"]
        self.assertEqual(subscriptions, [("/devices/fixture-device/command", 1), ("/devices/fixture-device/upgrade", 1)])
        publishes = self.broker.select("publish")
        status = [x for x in publishes if json.loads(x["payload"])["type"] == "status"]
        mood = [x for x in publishes if json.loads(x["payload"])["type"] == "moodPing"]
        self.assertGreaterEqual(len(status), 2)
        self.assertTrue(mood)
        self.assertTrue(all(x["qos"] == 1 and x["retain"] for x in status))
        self.assertTrue(all(x["qos"] == 1 and not x["retain"] for x in mood))
        self.assertTrue(all(x["topic"] == "/devices/fixture-device/status" for x in publishes))
        connect = self.broker.select("connect")[0]
        self.assertEqual(connect["flags"] & 0xc0, 0xc0)
        self.assertTrue(connect["flags"] & 2)
        self.assertEqual(connect["client_id"], "patchx-fixture-device")
        self.assertNotIn("synthetic-password", json.dumps(self.service.snapshot()))

    def test_real_received_command_has_internal_identity_and_never_runs_hardware(self):
        self.connected()
        self.broker.send("/devices/fixture-device/command", {"type": "brightness", "payload": {"value": 150}})
        wait_for(lambda: len(self.commands) == 1)
        self.assertEqual((self.commands[0].kind, self.commands[0].value), ("brightness", 150))
        self.assertTrue(self.service.accepts(self.commands[0]))
        forged = parse_command(b'{"type":"brightness","payload":{"value":150}}')
        self.assertFalse(self.service.accepts(forged))
        self.assertEqual(self.state.snapshot()["payload"]["brightness"], 0)
        wait_for(lambda: self.broker.select("puback"))

    def test_wrong_topic_retained_and_overlong_messages_rejected(self):
        self.connected()
        self.broker.send("/devices/other-device/command", {"type": "test", "payload": {"value": 5}})
        self.broker.send("/devices/fixture-device/command", {"type": "test", "payload": {"value": 5}}, header=0x33)
        self.broker.send("/devices/fixture-device/command", b' ' * 16500)
        wait_for(lambda: self.service.snapshot()["metrics"]["rejected_messages"] >= 3)
        self.assertEqual(self.commands, [])

    def test_disconnect_and_reconnect_fence_old_typed_command(self):
        self.connected()
        self.broker.send("/devices/fixture-device/command", {"type": "test", "payload": {"value": 5}})
        wait_for(lambda: self.commands)
        old = self.commands[0]
        self.broker.drop()
        wait_for(lambda: len(self.broker.select("connect")) >= 2)
        wait_for(lambda: self.service.snapshot()["connected"])
        self.assertFalse(self.service.accepts(old))
        self.assertEqual(len(self.broker.select("subscribe")), 2)
        self.broker.send("/devices/fixture-device/command", {"type": "test", "payload": {"value": 10}})
        wait_for(lambda: len(self.commands) == 2)
        self.assertTrue(self.service.accepts(self.commands[-1]))

    def test_old_actual_paho_client_callbacks_cannot_change_new_session(self):
        import paho.mqtt.client as mqtt
        self.connected()
        old_client = self.service.client
        self.broker.drop()
        wait_for(lambda: len(self.broker.select("connect")) >= 2)
        wait_for(lambda: self.service.snapshot()["connected"])
        current_session = self.service.snapshot()["session"]
        message = mqtt.MQTTMessage(mid=37, topic=b"/devices/fixture-device/command")
        message.payload = b'{"type":"brightness","payload":{"value":250}}'
        old_client.on_message(old_client, None, message)
        old_client.on_disconnect(old_client, None, None, None, None)
        old_client.on_subscribe(old_client, None, 1, [], None)
        self.assertEqual(self.commands, [])
        self.assertTrue(self.service.snapshot()["connected"])
        self.assertEqual(self.service.snapshot()["session"], current_session)

    def test_declared_huge_packet_is_closed_before_receiving_payload(self):
        self.connected()
        self.broker.declared_oversize()
        wait_for(lambda: self.service.snapshot()["metrics"]["oversized_packets"] == 1)
        wait_for(lambda: self.broker.select("peer_closed"))
        self.assertEqual(self.commands, [])

    def test_close_is_bounded_sends_disconnect_and_fences_commands(self):
        self.connected()
        self.broker.send("/devices/fixture-device/command", {"type": "test", "payload": {"value": 5}})
        wait_for(lambda: self.commands)
        before = time.monotonic()
        self.assertTrue(self.service.close())
        self.assertLess(time.monotonic() - before, 1.5)
        wait_for(lambda: self.broker.select("disconnect"))
        self.assertFalse(self.service.accepts(self.commands[0]))
        self.assertFalse(self.service.snapshot()["thread_alive"])
        self.assertTrue(self.service.close())

    def test_bad_suback_cannot_enable_commands(self):
        self.broker.suback = 0x80
        self.service.start()
        wait_for(lambda: self.broker.select("subscribe"))
        time.sleep(.12)
        self.assertFalse(self.service.snapshot()["connected"])
        self.assertEqual(self.commands, [])

    def test_queue_callback_rejection_and_exception_are_visible(self):
        def reject(command):
            return False
        self.service.on_command = reject
        self.connected()
        self.broker.send("/devices/fixture-device/command", {"type": "volume", "payload": {"value": 3}})
        wait_for(lambda: self.service.snapshot()["metrics"]["queue_rejections"] == 1)
        def fail(command):
            raise RuntimeError("synthetic-password")
        self.service.on_command = fail
        self.broker.send("/devices/fixture-device/command", {"type": "volume", "payload": {"value": 4}})
        wait_for(lambda: self.service.snapshot()["metrics"]["queue_rejections"] == 2)
        self.assertNotIn("synthetic-password", json.dumps(self.service.snapshot()))

    def test_missing_env_and_construction_do_not_connect(self):
        self.assertEqual(self.broker.select("connect"), [])
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(self.service.start())
        self.assertEqual(self.service.snapshot()["status"], "unavailable")
        self.assertEqual(self.broker.select("connect"), [])

    def test_close_while_broker_unreachable_and_restart_get_new_generation(self):
        self.service.port = 1
        self.service.start()
        wait_for(lambda: self.service.snapshot()["metrics"]["connection_errors"] >= 1)
        old_generation = self.service.snapshot()["generation"]
        before = time.monotonic()
        self.assertTrue(self.service.close())
        self.assertLess(time.monotonic() - before, 1.5)
        self.service.port = self.broker.port
        self.connected()
        self.assertGreater(self.service.snapshot()["generation"], old_generation)

    def test_without_puback_publish_queue_stays_bounded_and_close_exits(self):
        self.broker.puback = False
        self.service.status_interval = .015
        self.service.mood_interval = .015
        self.connected()
        wait_for(lambda: self.service.snapshot()["metrics"]["publish_errors"] >= 3)
        self.assertLessEqual(len(self.broker.select("publish")), 4)
        before = time.monotonic()
        self.assertTrue(self.service.close())
        self.assertLess(time.monotonic() - before, 1.5)

    def test_bad_status_identity_and_invalid_raw_state_are_never_published(self):
        bad = self.state.snapshot()
        bad["sn"] = "different-device"
        self.service.state_provider = lambda: bad
        self.connected()
        wait_for(lambda: self.service.snapshot()["metrics"]["publish_errors"] >= 1)
        bad["sn"] = "fixture-device"
        bad["payload"]["volume"] = 100
        wait_for(lambda: self.service.snapshot()["metrics"]["publish_errors"] >= 2)
        statuses = [x for x in self.broker.select("publish") if json.loads(x["payload"])["type"] == "status"]
        self.assertEqual(statuses, [])

    def test_blocked_state_provider_has_bounded_close_and_live_command_fence(self):
        entered, release = threading.Event(), threading.Event()
        def blocking_provider():
            entered.set()
            release.wait(4)
            return self.state.snapshot()
        self.addCleanup(release.set)
        self.service.state_provider = blocking_provider
        self.service.close_timeout = .08
        self.service.start()
        self.assertTrue(entered.wait(2))
        before = time.monotonic()
        self.assertFalse(self.service.close())
        self.assertLess(time.monotonic() - before, .3)
        self.assertEqual(self.service.snapshot()["status"], "closing")
        self.assertFalse(self.service.snapshot()["connected"])
        self.assertFalse(self.service.start())
        release.set()
        wait_for(lambda: not self.service.snapshot()["thread_alive"])
        self.assertTrue(self.service.close())
        self.assertEqual(self.broker.select("publish"), [])

    def test_blocked_queue_callback_late_command_is_fenced_after_close(self):
        entered, release = threading.Event(), threading.Event()
        def blocking_queue(command):
            entered.set()
            release.wait(4)
            self.commands.append(command)
        self.addCleanup(release.set)
        self.service.on_command = blocking_queue
        self.service.close_timeout = .08
        self.connected()
        self.broker.send("/devices/fixture-device/command", {"type": "volume", "payload": {"value": 2}})
        self.assertTrue(entered.wait(2))
        self.assertFalse(self.service.close())
        release.set()
        wait_for(lambda: self.commands)
        wait_for(lambda: not self.service.snapshot()["thread_alive"])
        self.assertFalse(self.service.accepts(self.commands[0]))
        self.assertTrue(self.service.close())

    def test_upgrade_subscription_delivers_explicit_unsupported_event(self):
        self.connected()
        self.broker.send("/devices/fixture-device/upgrade", {"type": "appUpdate", "payload": {
            "version": "synthetic-1.0", "url": "https://example.test/fixture.bin", "size": 1}})
        wait_for(lambda: self.commands)
        self.assertFalse(self.commands[0].supported)
        self.assertEqual(self.commands[0].kind, "appUpdate")
        self.assertTrue(self.service.accepts(self.commands[0]))
        self.assertEqual(self.service.snapshot()["metrics"]["unsupported_commands"], 1)

    def test_actual_paho_tls_uses_ca_and_hostname_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            cert, key = Path(directory) / "cert.pem", Path(directory) / "key.pem"
            # Ephemeral synthetic TLS fixture; no project/board credential is read.
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                            "-keyout", str(key), "-out", str(cert), "-subj", "/CN=127.0.0.1",
                            "-addext", "subjectAltName=IP:127.0.0.1"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(str(cert), str(key))
            client_context = ssl.create_default_context(cafile=str(cert))
            self.service.close()
            self.broker.close()
            self.broker = LoopbackBroker(tls_context=server_context)
            self.addCleanup(self.broker.close)
            self.service.port = self.broker.port
            self.service.tls = True
            self.service.tls_context = client_context
            self.connected()
            wait_for(lambda: self.broker.select("publish"))
            self.assertTrue(self.service.close())
            # An untrusted CA fails before MQTT CONNECT, proving verification.
            self.service.tls_context = ssl.create_default_context()
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ResourceWarning)
                self.service.start()
                wait_for(lambda: self.service.snapshot()["metrics"]["connection_errors"] >= 1)
                self.assertFalse(self.service.snapshot()["connected"])
                self.assertEqual(len(self.broker.select("connect")), 1)
                self.assertTrue(self.service.close())
                self.assertEqual([x for x in caught if x.category is ResourceWarning], [])

    def test_stalled_tls_handshake_does_not_use_90_second_keepalive_deadline(self):
        self.broker.close()
        self.broker = LoopbackBroker(stall_tls=True)
        self.addCleanup(self.broker.close)
        self.service.port = self.broker.port
        self.service.tls = True
        self.service.start()
        wait_for(lambda: self.broker.select("tcp_accept"))
        before = time.monotonic()
        self.assertTrue(self.service.close())
        self.assertLess(time.monotonic() - before, 1.5)


if __name__ == "__main__":
    unittest.main()
