"""Executable wrappers must not intercept independent shared SDK I/O."""
import errno
import json
import os
import subprocess
import tempfile
from pathlib import Path


def verify(binary: Path):
    assert not Path("/dev/video-fixture").exists(), "Shared I/O probe requires nonexistent host fixture path"
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "photo"
        result = subprocess.run(
            [str(binary), "--capture-once", "/iq-fixture", "/dev/video-fixture", str(output)],
            env=dict(os.environ, AIQ_FIXTURE="success", CAMERA_FIXTURE="format-success"),
            capture_output=True, text=True, timeout=12,
        )
        probes = [json.loads(line[len("FAKESHARED:"):]) for line in result.stderr.splitlines()
                  if line.startswith("FAKESHARED:")]
        assert probes == [dict(open_rc=-1, errno=errno.ENOENT)], probes
        assert result.returncode == 0, result
        capture = json.loads(result.stdout)
        assert capture["saved"] and capture["device_close_attempts"] == 2, capture
        return [dict(probes=probes, capture=capture, exit=result.returncode)]
