"""Opt-in non-real-time MQTT leaf. Only on_command may enqueue product work.

No JSON object is a core event: callbacks mint an immutable MqttCommand with
private origin and current generation/session. Consumers must call accepts()
again in the event worker before dispatch. on_command must be a nonblocking,
bounded queue insertion; state_provider must return an already cached state.
The service does not own a device port or execute any hardware command.
"""
from dataclasses import dataclass, field, replace
import errno
import json
import os
import re
import ssl
import threading
import time

from .robot_state import bounded_text, validate_status

MAX_PAYLOAD_BYTES = 16 * 1024
MAX_PACKET_BYTES = MAX_PAYLOAD_BYTES + 512
PAHO_VERSION = "2.1.0"
UPGRADE_TYPES = frozenset(("appUpdate", "firmwareUpdate", "systemUpdate"))
VALUE_TYPES = frozenset(("brightness", "volume", "touchWakeUpSwitch", "display",
                         "lcdback", "backenable", "test", "testEmote"))
INTENT_TYPES = frozenset(("divination", "decodeDreams"))


@dataclass(frozen=True)
class MqttCommand:
    kind: str
    value: object = None
    flag: object = None
    supported: bool = True
    intents: tuple = ()
    generation: int = 0
    session: int = 0
    _origin: object = field(default=None, repr=False, compare=False)


def _integer(value, low=0, high=2 ** 31 - 1):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("invalid MQTT integer")
    return value


def _unique_object(pairs):
    result = {}
    if len(pairs) > 8:
        raise ValueError("too many MQTT fields")
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate MQTT field")
        result[key] = value
    return result


def parse_command(payload):
    """Parse bounded wire bytes only; the returned command is not authorized."""
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_PAYLOAD_BYTES:
        raise ValueError("invalid MQTT payload size")
    try:
        data = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")))
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("invalid MQTT JSON") from None
    if type(data) is not dict or set(data) != {"type", "payload"}:
        raise ValueError("invalid MQTT command")
    kind, body = data["type"], data["payload"]
    if type(kind) is not str or len(kind) > 64 or type(body) is not dict:
        raise ValueError("invalid MQTT command fields")
    allowed = {"value"} if kind in VALUE_TYPES else {"flag"} if kind in INTENT_TYPES else set()
    if kind in UPGRADE_TYPES:
        allowed = {"value", "flag", "version", "url", "size"}
        for key, item in body.items():
            if key in ("version", "url"):
                bounded_text(item, 2048 if key == "url" else 128)
            elif key in ("value", "flag", "size"):
                _integer(item, 0, 2 ** 63 - 1 if key == "size" else 2 ** 31 - 1)
    if set(body) - allowed:
        raise ValueError("unknown MQTT payload field")
    if kind in VALUE_TYPES:
        maximum = {"brightness": 255, "volume": 15, "touchWakeUpSwitch": 1,
                   "lcdback": 255, "backenable": 1}.get(kind, 2 ** 31 - 1)
        value = _integer(body.get("value"), 0, maximum)
        if kind == "display" and value not in (0, 90, 180, 270):
            raise ValueError("invalid display rotation")
        # Preserve DeviceManager's missing-break intent sequence without I/O.
        if kind == "display":
            intents = (("display", value), ("lcdback", value), ("backenable", value == 1))
        elif kind == "lcdback":
            intents = (("lcdback", value), ("backenable", value == 1))
        elif kind in ("backenable", "touchWakeUpSwitch"):
            intents = ((kind, value == (1 if kind == "backenable" else 0)),)
        else:
            intents = ((kind, value),)
        return MqttCommand(kind, value=value, intents=intents)
    if kind in INTENT_TYPES:
        flag = _integer(body.get("flag"), 0, 1)
        mode = "fortune" if kind == "divination" else "dream"
        return MqttCommand(kind, flag=flag,
                           intents=(("listen_mode", ("start", mode) if flag == 1 else ("end", "")),))
    if kind == "unbind":
        return MqttCommand(kind, intents=(("unbind", True),))
    if kind in UPGRADE_TYPES or kind == "factoryReset":
        # Upgrade dispatch is commented in Android; recovery reboot has no Linux port.
        return MqttCommand(kind, supported=False)
    raise ValueError("unsupported MQTT command type")


def _paho():
    import paho.mqtt
    import paho.mqtt.client as mqtt
    if paho.mqtt.__version__ != PAHO_VERSION:
        raise ValueError("pinned MQTT dependency unavailable")
    return mqtt


def _bounded_client(mqtt, client_id, on_oversize):
    class BoundedClient(mqtt.Client):
        def _ssl_wrap_socket(self, tcp_sock):
            # Paho 2.1.0 uses keepalive (90s) as the handshake deadline and
            # leaves its temporary SSLSocket unclosed when verification fails.
            # This verified context owns the wrapped socket on every path.
            wrapped = None
            try:
                wrapped = self._ssl_context.wrap_socket(tcp_sock, server_hostname=self._host,
                                                        do_handshake_on_connect=False)
                wrapped.settimeout(self.connect_timeout)
                wrapped.do_handshake()
                return wrapped
            except Exception:
                (wrapped if wrapped is not None else tcp_sock).close()
                raise

        def _sock_recv(self, size):
            # Pinned Paho 2.1.0 decodes remaining_length before requesting body
            # bytes, but has no MQTT3 packet cap. Check here before allocation.
            if self._in_packet["remaining_length"] > MAX_PACKET_BYTES:
                on_oversize()
                raise OSError(errno.EMSGSIZE, "MQTT packet limit")
            return super()._sock_recv(min(size, MAX_PACKET_BYTES))
    return BoundedClient(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id,
                         clean_session=True, protocol=mqtt.MQTTv311, reconnect_on_failure=False)


class MqttService:
    def __init__(self, *, broker, port, tls, device_id, env_names, on_command,
                 state_provider, status_interval=5.0, mood_interval=3600.0,
                 reconnect_interval=1.0, close_timeout=1.25, tls_context=None):
        if type(broker) is not str or not broker or len(broker) > 253 or any(c.isspace() for c in broker) or "\x00" in broker:
            raise ValueError("invalid MQTT broker")
        if type(port) is not int or not 1 <= port <= 65535 or type(tls) is not bool:
            raise ValueError("invalid MQTT connection settings")
        if type(device_id) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", device_id):
            raise ValueError("invalid MQTT device identity")
        if type(env_names) is not dict or set(env_names) - {"username", "password", "client_id"} or not {"username", "password"} <= set(env_names):
            raise ValueError("invalid MQTT credential environment names")
        for value in env_names.values():
            if type(value) is not str or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", value):
                raise ValueError("invalid MQTT credential environment name")
        if not callable(on_command) or not callable(state_provider):
            raise ValueError("MQTT requires command queue and cached status provider")
        for duration in (status_interval, mood_interval, reconnect_interval, close_timeout):
            if type(duration) not in (int, float) or not .01 <= duration <= 86400:
                raise ValueError("invalid MQTT interval")
        if tls_context is not None and (not tls or not isinstance(tls_context, ssl.SSLContext) or
                                        tls_context.verify_mode != ssl.CERT_REQUIRED or not tls_context.check_hostname):
            raise ValueError("MQTT TLS must verify certificate and hostname")
        self.broker, self.port, self.tls, self.device_id = broker, port, tls, device_id
        self.env_names = env_names.copy()
        self.on_command, self.state_provider = on_command, state_provider
        self.status_interval, self.mood_interval = status_interval, mood_interval
        self.reconnect_interval, self.close_timeout = reconnect_interval, close_timeout
        self.tls_context = tls_context
        prefix = "/devices/" + device_id
        self.command_topics = (prefix + "/command", prefix + "/upgrade")
        self.status_topic = prefix + "/status"
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.thread = None
        self.client = None
        self._origin = object()
        self.generation = self.session = 0
        self.connected = False
        self.status, self.error = "disabled", ""
        self.metrics = {key: 0 for key in ("connections", "connection_errors", "disconnects", "received_messages",
                                         "rejected_messages", "queued_commands", "queue_rejections", "unsupported_commands",
                                         "oversized_packets", "status_publishes", "mood_publishes", "publish_errors")}

    def snapshot(self):
        with self.lock:
            return {"status": self.status, "connected": self.connected, "generation": self.generation,
                    "session": self.session, "error": self.error, "metrics": self.metrics.copy(),
                    "thread_alive": self.thread is not None and self.thread.is_alive()}

    def accepts(self, command):
        with self.lock:
            return (isinstance(command, MqttCommand) and command._origin is self._origin and
                    not self.stopping.is_set() and self.connected and
                    command.generation == self.generation and command.session == self.session)

    def _credentials(self):
        username = os.environ.get(self.env_names["username"], "")
        password = os.environ.get(self.env_names["password"], "")
        client_id = os.environ.get(self.env_names.get("client_id", ""), "") or "patchx-" + self.device_id
        for value, limit in ((username, 256), (password, 2048), (client_id, 256)):
            if not value:
                raise ValueError("MQTT credentials are not configured")
            bounded_text(value, limit)
        return username, password, client_id

    def start(self):
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return not self.stopping.is_set()
            try:
                mqtt, credentials = _paho(), self._credentials()
            except (ImportError, ValueError):
                self.status, self.error = "unavailable", "MQTT dependency or credential environment unavailable"
                return False
            self.stopping.clear()
            self.generation += 1
            generation = self.generation
            self.status, self.error = "connecting", ""
            self.thread = threading.Thread(target=self._run, args=(mqtt, credentials, generation),
                                           name="patchx-mqtt", daemon=True)
            self.thread.start()
            return True

    def close(self):
        with self.lock:
            self.stopping.set()
            self.generation += 1
            self.session += 1
            self.connected = False
            thread = self.thread
            self.status = "closing"
        # The worker owns all Paho socket operations. Bounded joins also cover
        # DNS/provider/queue callbacks outside this leaf's control.
        if thread is not None and thread is not threading.current_thread():
            thread.join(self.close_timeout)
        closed = thread is None or not thread.is_alive()
        with self.lock:
            self.status = "closed" if closed else "closing"
            if not closed:
                self.error = "MQTT worker did not exit within close deadline"
        return closed

    def _current(self, client, generation):
        return not self.stopping.is_set() and generation == self.generation and client is self.client

    def _metric(self, key):
        with self.lock:
            self.metrics[key] += 1

    def _invalidate(self, client, generation):
        with self.lock:
            if generation == self.generation and client is self.client:
                if self.connected:
                    self.metrics["disconnects"] += 1
                self.connected = False
                self.session += 1
                self.status = "reconnecting"

    def _configure(self, mqtt, credentials, generation):
        username, password, client_id = credentials
        client = _bounded_client(mqtt, client_id, lambda: self._metric("oversized_packets"))
        client.connect_timeout = 1.0
        client.max_inflight_messages_set(4)
        client.max_queued_messages_set(8)
        client.username_pw_set(username, password)
        if self.tls:
            client.tls_set_context(self.tls_context or ssl.create_default_context())
        subscribe_mid = []

        def on_connect(current, userdata, flags, reason_code, properties):
            with self.lock:
                if not self._current(current, generation) or reason_code.is_failure:
                    return
                self.session += 1
            result, mid = current.subscribe([(topic, 1) for topic in self.command_topics])
            if result != mqtt.MQTT_ERR_SUCCESS:
                current.disconnect()
            else:
                subscribe_mid[:] = [mid]

        def on_subscribe(current, userdata, mid, reasons, properties):
            with self.lock:
                if not self._current(current, generation) or subscribe_mid != [mid]:
                    return
                if len(reasons) != 2 or any(reason.is_failure or reason.value != 1 for reason in reasons):
                    self.error = "MQTT command subscriptions were refused"
                    current.disconnect()
                    return
                self.connected = True
                self.metrics["connections"] += 1
                self.status, self.error = "connected", ""

        def on_disconnect(current, userdata, flags, reason_code, properties):
            self._invalidate(current, generation)

        def on_message(current, userdata, message):
            with self.lock:
                if not self._current(current, generation) or not self.connected:
                    return
                self.metrics["received_messages"] += 1
                session = self.session
            try:
                if message.topic not in self.command_topics or message.retain:
                    raise ValueError("invalid MQTT command topic or retained replay")
                command = replace(parse_command(message.payload), generation=generation,
                                  session=session, _origin=self._origin)
            except (ValueError, TypeError, UnicodeError):
                self._metric("rejected_messages")
                return
            if not self.accepts(command):
                return
            if not command.supported:
                self._metric("unsupported_commands")
            try:
                accepted = self.on_command(command)
                self._metric("queue_rejections" if accepted is False else "queued_commands")
            except Exception:
                self._metric("queue_rejections")

        client.on_connect, client.on_subscribe = on_connect, on_subscribe
        client.on_disconnect, client.on_message = on_disconnect, on_message
        return client

    def _publish(self, mqtt, client, generation, mood=False):
        try:
            value = {"type": "moodPing", "payload": {}} if mood else validate_status(self.state_provider())
            if not mood and value["sn"] != self.device_id:
                raise ValueError("MQTT status identity mismatch")
            payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(payload) > MAX_PAYLOAD_BYTES:
                raise ValueError("MQTT status exceeds bound")
            with self.lock:
                if not self._current(client, generation) or not self.connected:
                    return
                result = client.publish(self.status_topic, payload, qos=1, retain=not mood)
            self._metric(("mood_publishes" if mood else "status_publishes")
                         if result.rc == mqtt.MQTT_ERR_SUCCESS else "publish_errors")
        except Exception:
            self._metric("publish_errors")

    def _run(self, mqtt, credentials, generation):
        try:
            while not self.stopping.is_set() and generation == self.generation:
                client = self._configure(mqtt, credentials, generation)
                with self.lock:
                    if self.stopping.is_set() or generation != self.generation:
                        break
                    self.client = client
                try:
                    result = client.connect(self.broker, self.port, keepalive=90)
                    if result != mqtt.MQTT_ERR_SUCCESS:
                        raise OSError("MQTT connect failed")
                    connected_deadline = time.monotonic() + 3.0
                    status_due = mood_due = 0.0
                    while not self.stopping.is_set() and generation == self.generation:
                        if client.loop(timeout=.05) != mqtt.MQTT_ERR_SUCCESS:
                            break
                        now = time.monotonic()
                        with self.lock:
                            connected = self.connected
                        if not connected:
                            if now >= connected_deadline:
                                break
                            continue
                        if now >= status_due:
                            self._publish(mqtt, client, generation)
                            status_due = now + self.status_interval
                        if now >= mood_due:
                            self._publish(mqtt, client, generation, mood=True)
                            mood_due = now + self.mood_interval
                except Exception:
                    self._metric("connection_errors")
                    with self.lock:
                        if generation == self.generation:
                            self.error = "MQTT connection attempt failed"
                finally:
                    self._invalidate(client, generation)
                    try:
                        client.disconnect()
                        client.loop(timeout=0)
                    except Exception:
                        pass
                    # Public disconnect() closes an established connection. If
                    # connect failed, release its socket through its destructor.
                    with self.lock:
                        if self.client is client:
                            self.client = None
                if self.stopping.wait(self.reconnect_interval):
                    break
        except Exception:
            with self.lock:
                if generation == self.generation:
                    self.error, self.status = "MQTT worker failed", "error"
        finally:
            with self.lock:
                self.connected = False
                self.session += 1
