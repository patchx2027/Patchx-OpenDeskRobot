"""The real capture core must requeue 30 good frames before saving frame 31."""
import errno
import json
import os
import subprocess
import tempfile
from pathlib import Path


def verify(binary: Path, prefix=()):
    records = []
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        for scenario in ("format-success", "format-padded", "format-initial-raw"):
            output = directory / scenario
            result = subprocess.run(
                [*prefix, str(binary), "--capture-once-640x480-nv12", "/dev/video-fixture", str(output)],
                env=dict(os.environ, CAMERA_FIXTURE=scenario), capture_output=True, text=True, timeout=12,
            )
            assert result.returncode == 0, result
            data = json.loads(result.stdout)
            assert data["frames"] == 31 and data["warmup_frames"] == 30, data
            assert data["sequence"] == 31 and data["saved"] and not data["cleanup_uncertain"], data
            assert data["streamon_attempts"] == data["streamoff_attempts"] == 1, data
            assert data["munmap_attempts"] == 4 and data["device_close_attempts"] == 2, data
            assert (data["initial_format"]["width"], data["initial_format"]["height"]) == (2592, 1944), data
            assert (data["width"], data["height"]) == (640, 480), data
            stride = 672 if scenario == "format-padded" else 640
            assert output.read_bytes() == bytes([42]) * (stride * 480 * 3 // 2)
            metadata = json.loads(Path(str(output) + ".json").read_text())
            assert metadata["sequence"] == 31 and metadata["warmup_frames"] == 30, metadata
            records.append(dict(scenario=scenario, diagnostic=data))
        for scenario in ("all-corrupt", "poll-timeout", "release-error"):
            output = directory / scenario
            result = subprocess.run(
                [*prefix, str(binary), "--capture-once-640x480-nv12", "/dev/video-fixture", str(output)],
                env=dict(os.environ, CAMERA_FIXTURE=scenario), capture_output=True, text=True, timeout=12,
            )
            data = json.loads(result.stdout)
            assert result.returncode == 1 and not data["saved"], data
            assert output.stat().st_size == 0, data
            assert data["streamoff_attempts"] == 1 and data["device_close_attempts"] == 2, data
            if scenario != "release-error":
                assert data["first_errno"] == errno.ETIMEDOUT and data["elapsed_ms"] < 11000, data
            records.append(dict(scenario=scenario, diagnostic=data))
    return records
