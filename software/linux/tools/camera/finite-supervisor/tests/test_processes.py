"""Real forks, signals and detached descendants exercise the supervision path.

Dropping pidfd identity, returning before waitpid or omitting orphan cleanup are
production regressions detected by receipts and independent /proc inspection.
"""
import json
import errno
import os
import re
import resource
import signal
import subprocess
import tempfile
import time
from pathlib import Path


def inherited_core_limit():
    resource.setrlimit(resource.RLIMIT_CORE, (1048576, 1048576))


def verify(supervisor: Path, children: dict, run_ms: int, term_ms: int, cleanup_ms: int,
           production_only=False):
    records = []
    with tempfile.TemporaryDirectory(dir="/dev/shm") as directory:
        directory = Path(directory)
        scenarios = [("ignore-term", 124)] if production_only else [
            ("success", 0), ("failure", 1), ("blocked", 124),
            ("ignore-term", 124), ("orphan", 1), ("log-flood", 1), ("churn", 1)]
        for scenario, expected in scenarios:
            base = directory / scenario
            stdout, stderr, receipt = [Path(str(base) + suffix) for suffix in (".out", ".err", ".jsonl")]
            arguments = [str(supervisor), "--capture-once", str(children[scenario]), "/lib-fixture",
                         "/helper-fixture", "/iq-fixture", "/dev/video-fixture", str(base) + ".frame",
                         str(stdout), str(stderr), str(receipt)]
            started = time.monotonic()
            with open("/dev/null", "rb") as borrowed:
                result = subprocess.run(
                    arguments, env=dict(os.environ, HDR_MODE="bad", USE_AS_FAKE_CAM="1"),
                    capture_output=True, text=True, timeout=(run_ms + cleanup_ms) / 1000 + 5,
                    preexec_fn=inherited_core_limit, pass_fds=(borrowed.fileno(),))
            elapsed = time.monotonic() - started
            assert receipt.exists(), (scenario, "missing supervisor receipt", result)
            receipts = [json.loads(line) for line in receipt.read_text().splitlines()]
            final = receipts[-1]
            assert result.returncode == expected, (scenario, result, receipts)
            assert not result.stdout and not result.stderr, (scenario, result)
            assert final["phase"] == "final" and final["identity_verified"], final
            assert final["main_reaped"] and final["descendants_cleanup_completed"], final
            assert not final["physical_cleanup_verified"], final
            assert not Path("/proc") .joinpath(str(final["main_pid"])).exists(), final
            assert final["main_start_ticks"] > 0, final
            if scenario in ("blocked", "ignore-term"):
                assert final["timed_out"] and final["first_error"] == "run-deadline", final
                assert run_ms / 1000 - 0.05 <= elapsed <= (run_ms + cleanup_ms) / 1000 + 1, final
                assert final["term_attempts"] > 0, final
                if scenario == "ignore-term":
                    assert final["kill_attempts"] > 0 and final["main_signal"] == 9, final
                    assert elapsed >= (run_ms + term_ms) / 1000 - 0.05, final
            if scenario == "orphan":
                assert final["adopted_reaped"] == 1 and final["kill_attempts"] > 0, final
                orphan = int(re.search(r"ORPHAN (\d+)", stdout.read_text()).group(1))
                assert not Path("/proc").joinpath(str(orphan)).exists(), final
            if scenario == "log-flood":
                assert stdout.stat().st_size <= 4 * 1024 * 1024 and final["main_signal"] == 25, final
            if scenario == "churn":
                assert final["adopted_reaped"] > 4 and final["kill_attempts"] > 0, final
                assert elapsed <= cleanup_ms / 1000 + 1, final
                for pid in re.findall(r"CHURN (\d+)", stdout.read_text()):
                    assert not Path("/proc").joinpath(pid).exists(), (pid, final)
            assert final["cleanup_uncertain"] == (scenario != "success"), final
            records.append(dict(scenario=scenario, elapsed_seconds=elapsed,
                                exit=result.returncode, receipts=receipts))
        if production_only:
            return records

        def capture_arguments(name, child="blocked"):
            base = directory / name
            return [str(supervisor), "--capture-once", str(children[child]), "/lib-fixture",
                    "/helper-fixture", "/iq-fixture", "/dev/video-fixture", str(base) + ".frame",
                    str(base) + ".out", str(base) + ".err", str(base) + ".jsonl"]

        arguments = capture_arguments("parent-term")
        report = Path(arguments[-1])
        process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 2
            while not report.exists() or not report.read_text():
                assert time.monotonic() < deadline, "supervisor did not publish started receipt"
                time.sleep(0.005)
            os.kill(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=cleanup_ms / 1000 + 3)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        final = json.loads(report.read_text().splitlines()[-1])
        assert process.returncode == 1 and not stdout and not stderr, (process.returncode, final)
        assert final["first_error"] == "interrupted" and not final["timed_out"], final
        assert final["main_reaped"] and final["descendants_cleanup_completed"], final
        assert final["cleanup_uncertain"] and not Path("/proc").joinpath(str(final["main_pid"])).exists(), final
        records.append(dict(scenario="parent-term", exit=process.returncode, receipt=final))

        sentinel = subprocess.Popen(["/bin/sleep", "30"])
        try:
            arguments = capture_arguments("unrelated-sibling", "ignore-term")
            result = subprocess.run(arguments, capture_output=True, timeout=(run_ms + cleanup_ms) / 1000 + 3)
            final = json.loads(Path(arguments[-1]).read_text().splitlines()[-1])
            assert result.returncode == 124 and sentinel.poll() is None, (result, final)
            assert Path("/proc").joinpath(str(sentinel.pid)).exists(), sentinel.pid
            records.append(dict(scenario="unrelated-sibling", exit=result.returncode,
                                sentinel_survived=True, receipt=final))
        finally:
            sentinel.terminate()
            sentinel.wait(timeout=2)

        # A caller with closed standard input/output must not leave CLOEXEC on
        # the newly opened log descriptors when dup2 happens to use the same FD.
        arguments = capture_arguments("closed-stdio", "success")

        def close_input_output():
            os.close(0)
            os.close(1)

        result = subprocess.run(arguments, capture_output=True, timeout=3,
                                preexec_fn=close_input_output)
        final = json.loads(Path(arguments[-1]).read_text().splitlines()[-1])
        assert result.returncode == 0 and final["main_reaped"], final
        assert "MAIN " in Path(arguments[8]).read_text(), final
        records.append(dict(scenario="closed-stdio", exit=result.returncode, receipt=final))

        for scenario in ["existing-receipt", "existing-stdout", "symlink-stdout"]:
            arguments = capture_arguments(scenario, "success")
            target = Path(arguments[-1] if scenario == "existing-receipt" else arguments[8])
            contents = "retained data\n"
            if scenario == "symlink-stdout":
                original = directory / "symlink-target"
                original.write_text(contents)
                target.symlink_to(original)
            else:
                target.write_text(contents)
            result = subprocess.run(arguments, capture_output=True, timeout=3)
            assert result.returncode == 125 and target.read_text() == contents, (scenario, result)
            if scenario != "existing-receipt":
                final = json.loads(Path(arguments[-1]).read_text().splitlines()[-1])
                assert final["first_error"] == "log-files" and final["first_errno"] == errno.EEXIST, final
                assert final["main_pid"] == 0 and not final["main_reaped"], final
            records.append(dict(scenario=scenario, exit=result.returncode, original_retained=True))
        probe = directory / "probe.jsonl"
        result = subprocess.run([str(supervisor), "--probe", str(probe)], capture_output=True, timeout=3)
        assert result.returncode == 0 and not result.stdout and not result.stderr, result
        assert json.loads(probe.read_text().splitlines()[-1])["capabilities_verified"], probe.read_text()
        records.append(dict(scenario="probe", exit=result.returncode))
        for arguments in [[], ["--help"], ["--probe", "relative"], ["--capture-once"]]:
            result = subprocess.run([str(supervisor), *arguments], capture_output=True, timeout=3)
            assert result.returncode == 2 and not result.stdout and not result.stderr, result
            records.append(dict(arguments=arguments, exit=result.returncode))
    return records
