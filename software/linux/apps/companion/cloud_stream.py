"""Bounded Qianfan SSE decoding and punctuation-aware speech segmentation."""
import json


def content_deltas(response, active):
    """Yield text only; reject truncated streams and never expose server errors."""
    total = 0
    event = []
    event_size = 0
    while active():
        line = response.readline(8193)
        total += len(line)
        if len(line) > 8192 or total > 262144:
            raise ValueError("Cloud stream exceeds limits")
        if not line:
            raise ValueError("Cloud stream ended before completion")
        line = line.decode("utf-8").rstrip("\r\n")
        if not line:
            if not event:
                continue
            data = "\n".join(event)
            event, event_size = [], 0
            if data == "[DONE]":
                return
            item = json.loads(data)
            choices = item.get("choices")
            if not isinstance(choices, list):
                raise ValueError("Invalid cloud event")
            if not choices:  # optional usage trailer
                continue
            choice = choices[0]
            delta = choice.get("delta", {})
            value = delta.get("content", "")
            if value is not None and not isinstance(value, str):
                raise ValueError("Invalid cloud text")
            if value:
                yield value
            # The terminal DONE marker is required even after finish_reason.
        elif line.startswith("data:"):
            value = line[5:].lstrip(" ")
            event_size += len(value)
            if event_size > 16384:
                raise ValueError("Cloud event exceeds limits")
            event.append(value)


class SentenceBuffer:
    """Start at natural punctuation; do not synthesize individual token pieces."""
    def __init__(self):
        self.pending = ""

    def feed(self, text, final=False):
        self.pending += text
        if len(self.pending) > 4096:
            raise ValueError("Unpunctuated reply exceeds limits")
        result = []
        start = 0
        for i, char in enumerate(self.pending):
            length = i + 1 - start
            if ((char in "。！？!?；;\n" and length >= 6) or
                    (char in "，,：:" and length >= 16)):
                result.append(self.pending[start:i + 1].strip())
                start = i + 1
        self.pending = self.pending[start:]
        if final and self.pending.strip():
            result.append(self.pending.strip())
            self.pending = ""
        return [part for part in result if part]
