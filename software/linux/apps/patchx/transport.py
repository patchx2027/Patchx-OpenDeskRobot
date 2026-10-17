"""PatchX hello fields on the existing bounded WebSocket adapter."""
import uuid

from apps.companion.transport import CloudTransport
from .protocol import enrich_hello


class PatchXCloudTransport:
    def __init__(self, config, on_message, on_error, client_ip, trace_factory=None):
        self.client_ip = client_ip
        self.trace_factory = trace_factory or (lambda: str(uuid.uuid4()))
        self.inner = CloudTransport(config, on_message, on_error)

    def connect(self):
        self.inner.connect()

    def send(self, message):
        if isinstance(message, dict) and message.get("type") == "hello":
            message = enrich_hello(message, self.client_ip, self.trace_factory())
        self.inner.send(message)

    def close(self):
        self.inner.close()
