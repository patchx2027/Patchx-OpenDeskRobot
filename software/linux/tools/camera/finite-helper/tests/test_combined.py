"""Exercise SDK lifetime around the real capture helper and real leaf core."""
import json
import os
import subprocess
import tempfile
from pathlib import Path


def verify(binary: Path, prefix=()):
    records = []
    with tempfile.TemporaryDirectory() as directory:
        for camera_case, expected in [("format-success", 0), ("zero-payload", 1),
                                      ("format-sfmt-error", 1), ("all-corrupt", 1),
                                      ("combined-cleanup-error", 1)]:
            output = Path(directory) / camera_case
            result = subprocess.run(
                [*prefix, str(binary), "--capture-once", "/iq-fixture", "/dev/video-fixture", str(output)],
                env=dict(os.environ, AIQ_FIXTURE="success", CAMERA_FIXTURE=camera_case),
                capture_output=True, text=True, timeout=12,
            )
            assert result.returncode == expected, result
            capture = json.loads(result.stdout)
            events = [json.loads(line[5:])["event"] for line in result.stderr.splitlines()
                      if line.startswith("FAKE:")]
            assert events == ["enum", "enum", "init", "prepare", "start", "stop", "deinit"], events
            receipts = [json.loads(line) for line in result.stderr.splitlines()
                        if line.startswith('{"aiq_finite":')]
            final = receipts[-1]
            assert final["phase"] == "final" and final["deinit_returned"], final
            assert final["first_error"] == ("none" if expected == 0 else "capture"), final
            assert final["cleanup_uncertain"] == bool(expected), final
            assert capture["device_close_attempts"] == 2, capture
            if expected == 0:
                assert capture["saved"] and capture["frames"] == 31 and capture["warmup_frames"] == 30
                assert output.stat().st_size == 460800
            elif camera_case == "combined-cleanup-error":
                assert capture["saved"] and capture["cleanup_uncertain"], capture
            else:
                assert not capture["saved"] and output.stat().st_size == 0, capture
            records.append(dict(scenario=camera_case, exit=result.returncode,
                                capture=capture, events=events, final=final))
    return records
