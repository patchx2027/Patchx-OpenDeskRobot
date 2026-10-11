#!/usr/bin/env python3
"""Report missing local inputs; never connect to hardware or application services."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    missing = []
    manifest = json.loads((ROOT / "docs/excluded-inputs.json").read_text())
    inputs = ["unityLibrary/build.gradle", "local.properties"]
    inputs.extend(item["path"] for item in manifest["files"])
    for relative in inputs:
        path = ROOT / relative
        if not path.is_file() or path.stat().st_size == 0:
            missing.append(relative)
    for relative in missing:
        print("MISSING:", relative)

    java_ok = False
    java = shutil.which("java")
    if java:
        result = subprocess.run([java, "-version"], capture_output=True, text=True)
        version = re.search(r'version "(\d+)', result.stderr + result.stdout)
        java_ok = result.returncode == 0 and version is not None and int(version[1]) >= 17
    if not java_ok:
        print("MISSING: JDK 17 or newer on PATH (required by AGP 8.11.0)")
    print("Input inventory only: optional engines may not need every listed asset.")
    print("Presence does not verify licenses, ABI compatibility, SDK installation or build success.")
    print("Supply your own authorization/identity files; do not copy another device's keys.")
    if missing or not java_ok:
        print("Source release is incomplete: missing inputs =", len(missing))
        return 2
    print("Listed inputs are present; follow README.md to attempt a local build.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
