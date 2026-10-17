"""Test-only dependency fixtures; never used by the product."""
import time


class FakeAudio:
    def __init__(self, config, on_error=None):
        self.closed = False
        self.recording = False
        self.rate = None
    def configure_output(self, rate):
        self.rate = rate
    def playback_busy(self):
        return False
    def stop(self):
        self.closed = True
    def stop_capture(self):
        self.recording = False


class FakeCodec:
    def encode(self, value):
        return b"opus"
    def close(self):
        pass


class FakeTransport:
    def __init__(self, config, on_message, on_error):
        self.message = on_message
        self.sent = []
        self.closed = False
    def connect(self):
        self.message({"type": "hello", "session_id": "fixture", "audio_params": {
            "format": "opus", "channels": 1, "sample_rate": 24000, "frame_duration": 60}})
    def send(self, value):
        self.sent.append(value)
    def close(self):
        self.closed = True


def wait_for(condition, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(.01)
    raise AssertionError("condition timed out")
