"""SDK boundaries are faked because no camera device exists on the build host.

Removing cleanup on failed start or permitting capture after failed prepare is a
production regression caught by the ordered lifecycle and exit-status assertions.
"""
import errno
import json
import os
import subprocess
from pathlib import Path


def verify(binary: Path, prefix=()):
    records = []
    cases = [
        ("isp-code-5", 0, ["enum", "enum", "init", "prepare", "start", "capture", "stop", "deinit"]),
        ("isp-code-21", 1, ["enum", "enum"]),
        ("isp-code-4", 1, ["enum", "enum"]),
        ("isp-code-6", 1, ["enum", "enum"]),
        ("no-sensor", 1, ["enum"]),
        ("wrong-sensor", 1, ["enum", "enum"]),
        ("wrong-isp", 1, ["enum", "enum"]),
        ("unterminated-name", 1, ["enum"]),
        ("ambiguous", 1, ["enum", "enum", "enum"]),
        ("init-null", 1, ["enum", "enum", "init"]),
        ("prepare-fail", 1, ["enum", "enum", "init", "prepare", "stop", "deinit"]),
        ("start-fail", 1, ["enum", "enum", "init", "prepare", "start", "stop", "deinit"]),
        ("capture-fail", 1, ["enum", "enum", "init", "prepare", "start", "capture", "stop", "deinit"]),
        ("stop-fail", 1, ["enum", "enum", "init", "prepare", "start", "capture", "stop", "deinit"]),
        ("success", 0, ["enum", "enum", "init", "prepare", "start", "capture", "stop", "deinit"]),
        ("deinit-errno", 0, ["enum", "enum", "init", "prepare", "start", "capture", "stop", "deinit"]),
    ]
    for scenario, expected_exit, expected_events in cases:
        result = subprocess.run(
            [*prefix, str(binary), "--capture-once", "/iq-fixture", "/dev/video-fixture", "/tmp/photo-fixture"],
            env=dict(os.environ, AIQ_FIXTURE=scenario), capture_output=True, text=True, timeout=10,
        )
        events = [json.loads(line[5:]) for line in result.stderr.splitlines() if line.startswith("FAKE:")]
        actual_events = [event["event"] for event in events]
        assert actual_events == expected_events, (scenario, actual_events, expected_events, result)
        assert result.returncode == expected_exit, (scenario, result)
        diagnostics = [json.loads(line) for line in result.stderr.splitlines() if line.startswith('{"aiq_finite":')]
        assert diagnostics, (scenario, result.stderr)
        final = diagnostics[-1]
        assert final["phase"] == "final", final
        assert final["cleanup_uncertain"] == (scenario in ("capture-fail", "stop-fail")), final
        assert final["deinit_returned"] == ("deinit" in expected_events), final
        sensors = [item for item in diagnostics if item["phase"] == "sensor"]
        if sensors:
            expected_code = 20 if scenario == "wrong-isp" else int(scenario[9:]) if scenario.startswith("isp-code-") else 5
            assert sensors[0]["public_isp_code"] == expected_code, (scenario, sensors)
            assert sensors[0]["mapped_sdk_isp_version"] == {4: 20, 5: 21, 6: 30}.get(expected_code, -1), sensors
            assert final["public_isp_code"] == expected_code, final
            assert final["mapped_sdk_isp_version"] == sensors[0]["mapped_sdk_isp_version"], final
        if scenario == "deinit-errno":
            assert final.get("deinit_errno") == errno.EBUSY, final
        if scenario.endswith("fail") or scenario == "init-null":
            assert final["first_error"] != "none", final
        else:
            expected_error = ("none" if scenario in ("success", "deinit-errno", "isp-code-5") else
                              "sensor-hardware" if scenario in ("wrong-isp", "isp-code-21", "isp-code-4", "isp-code-6") else "sensor-enumeration")
            assert final["first_error"] == expected_error, final
        records.append(dict(scenario=scenario, exit=result.returncode, events=events, final=final))
    for arguments in [[], ["--help"], ["--capture-once", "relative", "/dev/video0", "/tmp/out"],
                      ["--capture-once", "/iq", "/dev/video0", "/dev/video0"]]:
        result = subprocess.run([*prefix, str(binary), *arguments], capture_output=True, timeout=10)
        assert result.returncode == 2 and b"FAKE:" not in result.stderr, (arguments, result)
        records.append(dict(arguments=arguments, exit=result.returncode))
    return records
