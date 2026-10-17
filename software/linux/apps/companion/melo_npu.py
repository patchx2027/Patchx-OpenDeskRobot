"""RV1126B Melo hybrid synthesis; graph-specific 44.1 kHz decoder contract."""
import ctypes
import json
import queue
import threading
from pathlib import Path

RATE = 44100
HOP = 512
BUCKET = 256
HALO = 16
CORE = BUCKET - 2 * HALO


def scale_silence(samples, sample_rate=RATE):
    """Match sherpa 1.13.8 ScaleSilence(.2), once per complete generated batch."""
    import numpy as np
    x = np.asarray(samples, dtype=np.float32).reshape(-1)
    quiet = np.abs(x) <= np.float32(.01)
    edges = np.diff(np.concatenate(([False], quiet, [False])).astype(np.int8))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    threshold = int(sample_rate * .2)
    parts = []
    cursor = 0
    for start, end in zip(starts, ends):
        length = int(end - start)
        if length < threshold or (end == len(x) and length == threshold):
            continue
        count = int(np.float32(length) * np.float32(.2))
        parts.extend((x[cursor:start], x[start:start + count]))
        cursor = end
    parts.append(x[cursor:])
    return np.concatenate(parts)


def decode_chunks(latent, decoder):
    import numpy as np
    z = np.asarray(latent, dtype=np.float32)
    if z.ndim != 3 or z.shape[:2] != (1, 192) or not 0 < z.shape[2] <= 4000 or not np.isfinite(z).all():
        raise ValueError("Invalid Melo latent")
    bucket = getattr(decoder, "frames", BUCKET)
    if bucket not in (192, 256):
        raise ValueError("Unsupported Melo decoder bucket")
    core = bucket if z.shape[2] <= bucket else bucket - 2 * HALO
    length = z.shape[2]
    parts = []
    for start in range(0, length, core):
        end = min(length, start + core)
        left, right = max(0, start - HALO), min(length, end + HALO)
        count = right - left
        padded = np.zeros((1, 192, bucket), np.float32)
        padded[:, :, :count] = z[:, :, left:right]
        wave = np.asarray(decoder(padded, count), dtype=np.float32).reshape(-1)
        if len(wave) != bucket * HOP or not np.isfinite(wave).all():
            raise ValueError("Invalid Melo decoder output")
        parts.append(wave[(start - left) * HOP:(end - left) * HOP].copy())
    return np.concatenate(parts)


class MeloNpu:
    def __init__(self, root, sherpa, threads=2):
        root = Path(root)
        model = root / "vits-melo-tts-zh_en"
        hybrid = root / "melo-npu"
        self.decoder = NativeDecoder(hybrid)
        self.prefix = sherpa.OfflineTts(sherpa.OfflineTtsConfig(
            model=sherpa.OfflineTtsModelConfig(vits=sherpa.OfflineTtsVitsModelConfig(
                model=str(hybrid / "prefix.onnx"), tokens=str(model / "tokens.txt"),
                lexicon=str(model / "lexicon.txt"), dict_dir=str(model / "dict")), num_threads=threads,
                provider="cpu:" + str(Path(__file__).with_name("melo-cpu.conf"))),
            rule_fsts=",".join(str(model / name) for name in ("date.fst", "number.fst", "phone.fst")),
            max_num_sentences=1, silence_scale=1.0))

    def synthesize(self, text, on_audio=None, cancelled=None):
        import numpy as np
        # One queued latent plus one in flight; one decoder owns native context.
        jobs = queue.Queue(maxsize=1)
        stop = threading.Event()
        producer_done = threading.Event()
        parts, errors = [], []
        emitted = 0

        def check_cancelled():
            if cancelled is not None and cancelled():
                raise RuntimeError("Melo synthesis cancelled")

        def consumer():
            nonlocal emitted
            total = 0
            try:
                while not stop.is_set():
                    check_cancelled()
                    try:
                        z = jobs.get(timeout=.02)
                    except queue.Empty:
                        if producer_done.is_set():
                            return
                        continue
                    wave = scale_silence(decode_chunks(z, self.decoder))
                    total += len(wave)
                    if total > RATE * 45:
                        raise ValueError("Synthesized reply too long")
                    check_cancelled()
                    if on_audio is None:
                        parts.append(wave)
                    else:
                        on_audio(wave, RATE)
                    emitted += 1
            except BaseException as error:
                errors.append(error)
                stop.set()

        def callback(samples, progress):
            try:
                check_cancelled()
                values = np.asarray(samples, dtype=np.float32)
                if (values.size == 0 or values.size % 192 or values.size > 192 * 4000 or
                        not np.isfinite(values).all()):
                    raise ValueError("Invalid Melo prefix output")
                # Sherpa callback storage belongs to producer; always copy it.
                latent = values.reshape(1, 192, -1).copy()
                latent.flags.writeable = False
                while not stop.is_set():
                    check_cancelled()
                    try:
                        jobs.put(latent, timeout=.02)
                        return 1
                    except queue.Full:
                        pass
                return 0
            except BaseException as error:
                errors.append(error)
                stop.set()
                return 0

        worker = threading.Thread(target=consumer, name="melo-decoder-pipeline")
        worker.start()
        try:
            self.prefix.generate(text, sid=0, speed=1.0, callback=callback)
        except BaseException as error:
            errors.append(error)
            stop.set()
        finally:
            producer_done.set()
            # Native decoder call must finish before its context can be reused.
            # No daemon leak or returning while a native call is still in flight.
            worker.join()
        if errors:
            raise RuntimeError("Melo pipelined synthesis failed") from errors[0]
        if not emitted:
            raise ValueError("Empty Melo synthesis")
        return (np.concatenate(parts), RATE) if on_audio is None else None

    def synthesize_stream(self, text, on_audio, cancelled=None):
        return self.synthesize(text, on_audio=on_audio, cancelled=cancelled)


class NativeDecoder:
    """Native ABI definition is paired with scripts/speech-npu/melo_decoder.cc."""
    def __init__(self, root):
        self.context = None
        self.library = ctypes.CDLL(str(Path(root) / "libmelo_decoder.so"))
        lib = self.library
        pointer = ctypes.POINTER(ctypes.c_float)
        lib.melo_decoder_create.argtypes = [ctypes.c_char_p]
        lib.melo_decoder_create.restype = ctypes.c_void_p
        lib.melo_decoder_run.argtypes = [ctypes.c_void_p, pointer, ctypes.c_uint, pointer]
        lib.melo_decoder_run.restype = ctypes.c_int
        lib.melo_decoder_destroy.argtypes = [ctypes.c_void_p]
        lib.melo_decoder_destroy.restype = ctypes.c_int
        lib.melo_decoder_error.argtypes = []
        lib.melo_decoder_error.restype = ctypes.c_char_p
        lib.melo_decoder_frames.argtypes = [ctypes.c_void_p]
        lib.melo_decoder_frames.restype = ctypes.c_uint
        selection = Path(root) / "decoder.json"
        filename = "decoder-masked-256.rknn"
        if selection.exists():
            if selection.stat().st_size > 4096:
                raise ValueError("Invalid Melo decoder selection")
            settings = json.loads(selection.read_text())
            filename = settings.get("model") if isinstance(settings, dict) else None
            if filename not in ("decoder-masked-192.rknn", "decoder-masked-256.rknn"):
                raise ValueError("Unsupported Melo decoder model")
        self.context = lib.melo_decoder_create(str(Path(root) / filename).encode())
        if not self.context:
            raise RuntimeError("Melo decoder initialization failed")
        self.frames = int(lib.melo_decoder_frames(self.context))
        if self.frames not in (192, 256):
            self.close()
            raise ValueError("Unsupported Melo decoder bucket")

    def __call__(self, latent, valid_length):
        import numpy as np
        data = np.ascontiguousarray(latent, dtype=np.float32)
        if data.shape != (1, 192, self.frames) or not 0 < valid_length <= self.frames or not self.context:
            raise ValueError("Invalid decoder input")
        output = np.empty(self.frames * HOP, np.float32)
        pointer = ctypes.POINTER(ctypes.c_float)
        code = self.library.melo_decoder_run(self.context, data.ctypes.data_as(pointer),
                                            valid_length, output.ctypes.data_as(pointer))
        if code:
            raise RuntimeError("Melo NPU inference failed")
        return output

    def close(self):
        if self.context:
            self.library.melo_decoder_destroy(self.context)
            self.context = None

    def __del__(self):
        self.close()
