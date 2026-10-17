"""Fixed RK3568 OV5695 RAM adapter: supervised SDK -> exact NV12 -> bounded JPEG.

The command is opt-in and board-specific. It never flashes or starts a daemon.
Each call owns an empty temporary directory supplied by CommandCameraBackend.
"""
import argparse
import hashlib
import json
import os
import signal
from pathlib import Path
import stat
import subprocess
import time

KERNEL = "5.10.160-rt89-g9f9e9d18574d-dirty"
_cancelled = False
LOCKED_INPUTS = {
    "bin/aiq-finite.aarch64": "99e332837b4c5db139ccd97487c8b9d768d81644894cea9554fc9dc8df453e82",
    "bin/aiq-supervisor.aarch64": "f3460130788b931065c0bf7ff8496d6794f645a52219bf4f5d0f0ad2e9d0da6d",
    "iq/ov5695_TongJu_CHT842-MD.json": "8619ad612c492c64079ee5e9a012d8474bf4b396e611b615aa16ab8b3e52ce72",
    "lib/ld-linux-aarch64.so.1": "b9b0d356a67d91b3309c6b64ff654a4a42bd44301d28592952a3f87c930c7dea",
    "lib/libc.so.6": "9f8be7a87463ffb5b723c525b4b527dec519716e5283c9509b8df002d7a92151",
    "lib/libgcc_s.so.1": "020f45f8231ddd6bf3edbc925e68089dba1a0c16923a5e82afacf80d2995b14b",
    "lib/libm.so.6": "c59c7612710cf20b9a36ccd266e23aa3c4d13118ae9a672c7baa2241716cbd5e",
    "lib/librkaiq.so": "3ecc983fd76e4f795e0072071c365754c86bbb12d5878af869d45e1e4cfd12d8",
    "lib/libstdc++.so.6": "84567b69c610a9913481f7e676302380e9e03f0d630f9b67909751d226d08288",
}


def _expect(data, key, expected):
    if type(data.get(key)) is not type(expected) or data[key] != expected:
        raise ValueError("native camera receipt rejected")


def validate_capture_receipts(supervisor, helper, frame, *, raw_bytes, started_ns, now_ns):
    if not supervisor or not helper or type(frame) is not dict:
        raise ValueError("native camera receipts missing")
    last = supervisor[-1]
    for key, value in {"phase": "final", "aiq_supervisor": 1, "first_error": "none", "first_errno": 0,
            "cleanup_errno": 0, "main_exit": 0, "main_signal": 0, "main_reaped": True,
            "descendants_cleanup_completed": True, "capabilities_verified": True, "identity_verified": True,
            "timed_out": False, "cleanup_uncertain": False, "term_attempts": 0, "kill_attempts": 0}.items():
        _expect(last, key, value)
    last = helper[-1]
    for key, value in {"phase": "final", "aiq_finite": 1, "first_error": "none", "first_rc": 0,
            "first_errno": 0, "stop_rc": 0, "deinit_returned": True, "cleanup_uncertain": False,
            "public_isp_code": 5, "mapped_sdk_isp_version": 21}.items():
        _expect(last, key, value)
    phases = []
    for item in helper:
        if item.get("phase") in ("init", "prepare", "start", "capture", "stop", "deinit") and item.get("action") == "return":
            _expect(item, "rc", 0)
            phases.append(item["phase"])
    if phases != ["init", "prepare", "start", "capture", "stop", "deinit"]:
        raise ValueError("native camera lifecycle incomplete")
    for key, value in {"diagnostic": "camera-bounded-format-v3", "format_verified": True, "saved": True,
            "width": 640, "height": 480, "native_format": 842094158, "frames": 31, "warmup_frames": 30,
            "corrupt_frames": 0, "first_errno": 0, "cleanup_uncertain": False, "timestamp_monotonic": 1,
            "streamoff_attempts": 1, "streamoff_errno": 0, "munmap_attempts": 4, "munmap_errno": 0,
            "device_close_attempts": 2, "device_close_errno": 0, "output_close_errno": 0,
            "fd_flags_restore_errno": 0}.items():
        _expect(frame, key, value)
    plane = {"stride": 640, "sizeimage": 460800, "payload": 460800, "allocation": 460800, "offset": 0}
    if type(frame.get("planes")) is not list or len(frame["planes"]) != 1:
        raise ValueError("native camera plane count rejected")
    for key, value in plane.items():
        _expect(frame["planes"][0], key, value)
    stamp = frame.get("timestamp_ns")
    if type(stamp) is not int or not started_ns <= stamp <= now_ns or raw_bytes != 460800:
        raise ValueError("native camera frame length or freshness rejected")
    return {"width": 640, "height": 480, "captured_monotonic_ns": stamp,
            "cleanup_uncertain": False, "physical_image_quality_verified": False,
            "matrix": "full-range BT601 assumed"}


def _regular(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError("native camera file rejected")
        data = stream.read(maximum + 1)
        if len(data) != info.st_size or len(data) > maximum:
            raise ValueError("native camera file changed")
        return data


def _lines(path, marker):
    result = []
    for line in _regular(path, 4 * 1024 * 1024).splitlines():
        if line.startswith(b"{"):
            try:
                item = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if type(item) is dict and item.get(marker) == 1:
                result.append(item)
    return result


def _hardware_idle(wait_s=0):
    expected = {
        "/sys/class/regulator/regulator.1/state": "disabled",
        "/sys/class/regulator/regulator.1/num_users": "0",
        "/sys/bus/i2c/devices/2-0036/power/runtime_status": "suspended",
        "/sys/bus/platform/devices/rkisp-vir0/power/runtime_status": "suspended",
        "/sys/bus/platform/devices/fdff0000.rkisp/power/runtime_status": "suspended",
    }
    deadline = time.monotonic() + wait_s
    while True:
        observed = {path: Path(path).read_text(encoding="ascii").strip() for path in expected}
        if observed == expected:
            return
        if time.monotonic() >= deadline:
            raise ValueError("native camera idle baseline rejected")
        time.sleep(.05)


def _run_supervisor(argv):
    """TERM stops this command's result, but never abandons the owned supervisor.

    The native supervisor gets the same process-group TERM and owns its child
    identities and bounded drain. The outer command adapter allows six seconds
    to finish that five-second cleanup before escalation, then latches failure.
    """
    if _cancelled:
        raise ValueError("native camera command cancelled")
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    if _cancelled:
        # Close the check-to-spawn race if TERM arrived just before fork/exec.
        process.send_signal(signal.SIGTERM)
    status = process.wait()
    if status or _cancelled:
        raise ValueError("native camera supervision failed or cancelled")


def capture(isp_root, encoder, output, budget):
    if os.uname().release != KERNEL or Path("/proc/sys/kernel/tainted").read_text().strip() != "0":
        raise ValueError("native camera platform rejected")
    if Path("/proc/sys/kernel/core_pattern").read_text().startswith("|"):
        raise ValueError("native camera core handler rejected")
    if not stat.S_ISCHR(os.stat("/dev/video0").st_mode):
        raise ValueError("native camera device rejected")
    if isp_root.is_symlink() or not isp_root.is_absolute() or not encoder.is_absolute():
        raise ValueError("native camera runtime path rejected")
    info = output.lstat()
    if (not output.is_absolute() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or
            stat.S_IMODE(info.st_mode) != 0o700 or any(output.iterdir())):
        raise ValueError("native camera output ownership rejected")
    for relative, expected in LOCKED_INPUTS.items():
        data = _regular(isp_root / relative, 16 * 1024 * 1024)
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("native camera input identity rejected")
    _hardware_idle()
    supervisor = isp_root / "bin/aiq-supervisor.aarch64"
    _run_supervisor([str(supervisor), "--probe", str(output / "capabilities.jsonl")])
    probe = _lines(output / "capabilities.jsonl", "aiq_supervisor")
    if not probe or probe[-1].get("capabilities_verified") is not True:
        raise ValueError("native camera supervision unavailable")
    started = time.monotonic_ns()
    _run_supervisor([str(supervisor), "--capture-once", str(isp_root / "lib/ld-linux-aarch64.so.1"),
        str(isp_root / "lib"), str(isp_root / "bin/aiq-finite.aarch64"), str(isp_root / "iq"),
        "/dev/video0", str(output / "frame.nv12"), str(output / "stdout.log"),
        str(output / "stderr.log"), str(output / "supervisor.jsonl")])
    raw = _regular(output / "frame.nv12", 460800)
    frame = json.loads(_regular(output / "frame.nv12.json", 65536))
    result = validate_capture_receipts(_lines(output / "supervisor.jsonl", "aiq_supervisor"),
        _lines(output / "stderr.log", "aiq_finite"), frame, raw_bytes=len(raw),
        started_ns=started, now_ns=time.monotonic_ns())
    _hardware_idle(wait_s=3)
    encoded = subprocess.run([str(encoder), "--input", str(output / "frame.nv12"),
        "--output", str(output / "frame.jpg"), "--width", "640", "--height", "480", "--stride", "640",
        "--max-bytes", str(budget)], check=True, capture_output=True, timeout=3)
    summary = json.loads(encoded.stdout)
    jpeg = _regular(output / "frame.jpg", budget)
    if summary.get("ok") is not True or summary.get("jpeg_bytes") != len(jpeg) or not jpeg:
        raise ValueError("native camera encoder rejected")
    if _cancelled:
        raise ValueError("native camera command cancelled")
    result.update(ok=True, jpeg_bytes=len(jpeg), jpeg_sha256=hashlib.sha256(jpeg).hexdigest(),
                  raw_sha256=hashlib.sha256(raw).hexdigest(), quality=summary["quality"])
    with (output / "result.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, allow_nan=False)
    return result


def main(argv=None):
    global _cancelled
    _cancelled = False
    def stop(*_):
        global _cancelled
        _cancelled = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    parser = argparse.ArgumentParser(description="Finite RK3568 PatchX camera adapter")
    parser.add_argument("--isp-root", type=Path, required=True)
    parser.add_argument("--encoder", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-jpeg-bytes", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if not 128 <= args.max_jpeg_bytes <= 65536:
            raise ValueError("invalid JPEG budget")
        capture(args.isp_root, args.encoder, args.output_dir, args.max_jpeg_bytes)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print("native camera capture failed; no image accepted", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
