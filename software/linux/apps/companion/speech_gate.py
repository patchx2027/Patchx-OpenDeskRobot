"""Bounded PCM energy onset gate, not a linguistic VAD or wake-word detector."""
import audioop
from collections import deque


class SpeechGate:
    def __init__(self, minimum_rms=24):
        self.minimum_rms = minimum_rms
        self.noise = 8.0
        self.history = deque(maxlen=8)  # 480ms at normal 60ms capture frames.
        self.votes = deque(maxlen=4)

    def feed(self, pcm):
        if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2 or len(pcm) > 1920:
            raise ValueError("Invalid speech gate PCM")
        self.history.append(pcm)
        centered = audioop.bias(pcm, 2, -audioop.avg(pcm, 2))
        rms = audioop.rms(centered, 2)
        voiced = rms >= max(self.minimum_rms, self.noise * 3)
        self.votes.append(voiced)
        if not voiced:
            self.noise = .98 * self.noise + .02 * min(rms, self.minimum_rms)
        if sum(self.votes) >= 3:
            result = list(self.history)
            self.history.clear()
            self.votes.clear()
            return result
        return None
