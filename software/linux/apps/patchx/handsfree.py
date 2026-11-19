"""Local wake-word and speech detection over in-memory mono PCM16."""
from pathlib import Path
import audioop
import struct


class SpeechDetector:
    def __init__(self, directory):
        import sherpa_onnx

        root = Path(directory)
        self.spotter = sherpa_onnx.KeywordSpotter(
            tokens=str(root / "tokens.txt"), encoder=str(root / "encoder.onnx"),
            decoder=str(root / "decoder.onnx"), joiner=str(root / "joiner.onnx"),
            keywords_file=str(root / "keywords.txt"), num_threads=1,
            keywords_score=3.0, keywords_threshold=0.1, max_active_paths=10)
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


class BargeInGate:
    """Count energy-qualified speech; only sustained silence resets the count."""

    def __init__(self, min_rms=1000, speech_frames=5, silence_frames=12):
        self.min_rms = min_rms
        self.required_speech = speech_frames
        self.required_silence = silence_frames
        self.reset()

    def reset(self):
        self.voice_frames = 0
        self.silence_frames = 0
        self.frames = 0
        self.last_rms = 0
        self.peak_rms = 0
        self.energy_passes = 0
        self.vad_passes = 0
        self.triggered = False

    def accept(self, pcm, speech):
        if not isinstance(pcm, bytes) or len(pcm) != 1920:
            raise ValueError("barge-in requires 960 mono PCM16 samples")
        self.frames += 1
        self.last_rms = audioop.rms(pcm, 2)
        self.peak_rms = max(self.peak_rms, self.last_rms)
        energetic = self.last_rms >= self.min_rms
        self.energy_passes += int(energetic)
        self.vad_passes += int(bool(speech))
        if energetic and speech:
            self.voice_frames += 1
            self.silence_frames = 0
        else:
            self.silence_frames += 1
            if self.silence_frames >= self.required_silence:
                self.voice_frames = 0
        if not self.triggered and self.voice_frames >= self.required_speech:
            self.triggered = True
            return True
        return False

    def snapshot(self):
        return {"min_rms": self.min_rms, "last_rms": self.last_rms,
                "peak_rms": self.peak_rms, "frames": self.frames,
                "energy_passes": self.energy_passes, "vad_passes": self.vad_passes,
                "voice_frames": self.voice_frames, "silence_frames": self.silence_frames,
                "triggered": self.triggered}


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
