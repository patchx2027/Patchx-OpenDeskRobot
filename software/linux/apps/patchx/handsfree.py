"""Local wake-word and speech detection over in-memory mono PCM16."""
from pathlib import Path
import struct


class SpeechDetector:
    def __init__(self, directory):
        import sherpa_onnx

        root = Path(directory)
        self.spotter = sherpa_onnx.KeywordSpotter(
            tokens=str(root / "tokens.txt"), encoder=str(root / "encoder.onnx"),
            decoder=str(root / "decoder.onnx"), joiner=str(root / "joiner.onnx"),
            keywords_file=str(root / "keywords.txt"), num_threads=1)
        self.stream = self.spotter.create_stream()
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(root / "silero_vad.onnx")
        config.silero_vad.threshold = 0.65
        config.silero_vad.min_speech_duration = 0.32
        config.sample_rate = 16000
        config.num_threads = 1
        self.vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=5)

    def _samples(self, pcm):
        return [sample / 32768 for (sample,) in struct.iter_unpack("<h", pcm)]

    def reset_wake(self):
        self.spotter.reset_stream(self.stream)

    def wake(self, pcm):
        self.stream.accept_waveform(16000, self._samples(pcm))
        while self.spotter.is_ready(self.stream):
            self.spotter.decode_stream(self.stream)
            if self.spotter.get_result(self.stream):
                self.reset_wake()
                return True
        return False

    def reset_speech(self):
        self.vad.reset()

    def speech(self, pcm):
        self.vad.accept_waveform(self._samples(pcm))
        return self.vad.is_speech_detected()

def load_wake_prompt(path):
    import wave
    from apps.companion.audio import PcmAudio

    try:
        with wave.open(path, "rb") as stream:
            rate = stream.getframerate()
            count = stream.getnframes()
            if (stream.getnchannels() != 1 or stream.getsampwidth() != 2
                    or rate not in (8000, 16000, 22050, 24000, 44100, 48000)
                    or not 0 < count <= rate * 3):
                raise ValueError("wake prompt must be mono PCM16 and at most three seconds")
            data = stream.readframes(count)
            if len(data) != count * 2:
                raise ValueError("incomplete wake prompt")
            return PcmAudio(data, rate)
    except (wave.Error, EOFError) as exc:
        raise ValueError("invalid wake prompt WAV") from exc
