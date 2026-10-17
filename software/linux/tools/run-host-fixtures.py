#!/usr/bin/env python3
"""Explicitly run synthetic native host fixtures; no camera SDK or board device is opened."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def module(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--targets", nargs="+", choices=("supervisor", "jpeg", "aiq"), required=True)
    args = parser.parse_args()
    build = args.build_dir.resolve()
    manifest = json.loads((build / "build-manifest.json").read_text())
    if manifest.get("host_fixtures") is not True:
        parser.error("use a directory produced by build-native.py --host-fixtures")
    results = {}
    if "supervisor" in args.targets:
        suite = module(HERE / "camera/finite-supervisor/tests/test_processes.py")
        children = {name: build / ("child-" + name) for name in
                    ("success", "failure", "blocked", "ignore-term", "orphan", "log-flood", "churn")}
        results["supervisor"] = suite.verify(build / "supervisor.fixture", children, 300, 200, 1000)
        result = subprocess.run([str(build / "reap-budget.fixture")], check=True,
                                capture_output=True, text=True, timeout=3)
        results["reap-budget"] = json.loads(result.stdout)
    if "jpeg" in args.targets:
        subprocess.run([sys.executable, str(HERE / "jpeg/test_encoder.py"),
                        str(build / "nv12-to-jpeg")], check=True)
        results["jpeg"] = {"passed": True}
    if "aiq" in args.targets:
        tests = HERE / "camera/finite-helper/tests"
        for name, executable in (("lifecycle", "lifecycle.fixture"),
                                 ("warmup", "capture.fixture"),
                                 ("format", "capture.fixture"),
                                 ("combined", "combined.fixture"),
                                 ("shared_io", "shared-io.fixture")):
            results[name] = module(tests / ("test_" + name + ".py")).verify(build / executable)
    (build / "fixture-results.json").write_text(json.dumps(results, indent=2) + "\n")
    print("Requested host fixtures passed; this is not hardware validation.")


if __name__ == "__main__":
    main()
