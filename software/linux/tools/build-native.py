#!/usr/bin/env python3
"""Compile native tools or host fixtures. Never download, deploy or execute them."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess

HERE = Path(__file__).resolve().parent
HELPER = HERE / "camera/finite-helper"
SUPERVISOR = HERE / "camera/finite-supervisor"
CAPTURE = HELPER / "capture-source"
C_SOURCES = [CAPTURE / name for name in (
    "modules/capture/src/capture.c",
    "adapters/capture/v4l2/src/v4l2_capture.c",
    "adapters/capture/v4l2/src/v4l2_format.c",
)]
WRAPS = ["-Wl,--wrap=" + name for name in
         ("open", "close", "ioctl", "mmap", "munmap", "poll", "__poll_chk", "write")]
WARNINGS = ["-Wall", "-Wextra", "-Werror", "-Wpedantic"]
CHILDREN = ["success", "failure", "blocked", "ignore-term", "orphan", "log-flood", "churn"]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Builder:
    def __init__(self, args):
        self.args = args
        self.output = args.output.resolve()
        self.cc = args.cross_prefix + "gcc"
        self.cxx = args.cross_prefix + "g++"
        self.common = (["--sysroot=" + str(args.sysroot.resolve())] if args.sysroot else [])
        self.commands = []
        self.static = [] if args.dynamic_utilities or args.host_fixtures else ["-static"]
        # Bind first-party helper inputs, including fixtures, without copying an SDK.
        self.sources = {str(p.relative_to(HERE)): digest(p) for p in sorted(HELPER.rglob("*"))
                        if p.is_file() and p.suffix in (".c", ".cpp", ".h", ".py")}
        self.sources["build-native.py"] = digest(Path(__file__).resolve())
        encoded = json.dumps(self.sources, sort_keys=True, indent=2).encode()
        self.source_sha = hashlib.sha256(encoded).hexdigest()

    def compile(self, argv):
        command = [str(part) for part in argv]
        print(shlex.join(command), flush=True)
        self.commands.append(command)
        subprocess.run(command, check=True)

    def c(self, *args):
        self.compile([self.cc, *self.common, "-std=gnu11", "-O2", *WARNINGS, *args])

    def cpp(self, *args):
        self.compile([self.cxx, *self.common, "-std=c++17", "-O2", *WARNINGS, *args])

    def capture_flags(self):
        return ["-I" + str(CAPTURE / "modules/capture/include"),
                "-I" + str(CAPTURE / "adapters/capture/v4l2/include"),
                '-DCAPTURE_SOURCE_SHA="' + self.source_sha + '"']

    def sdk_flags(self):
        sdk = self.args.rkaiq_sdk.resolve()
        directories = [sdk / part for part in (
            "include", "include/uAPI2", "include/common", "include/algos",
            "include/xcore", "include/iq_parser", "include/iq_parser_v2", ".")]
        for directory in directories:
            if not directory.is_dir():
                raise ValueError("RKAIQ include directory missing: " + str(directory))
        return ["-DISP_HW_V21", *[part for directory in directories
                                 for part in ("-isystem", str(directory))]]

    def launcher(self):
        if self.args.host_fixtures:
            raise ValueError("Launcher fixtures have separate namespace runners; see tools/README.md")
        for name in ("launch-product", "launch-diagnostic"):
            self.c(*self.static, HERE / "launcher" / (name + ".c"), "-o", self.output / name)

    def supervisor(self):
        if not self.args.host_fixtures:
            self.c(*self.static, SUPERVISOR / "supervisor.c", "-o", self.output / "aiq-supervisor")
            return
        for mode, name in enumerate(CHILDREN):
            self.c("-DCHILD_MODE=" + str(mode), SUPERVISOR / "tests/child.c",
                   "-o", self.output / ("child-" + name))
        fixture_flags = ["-DRUN_MS=300", "-DTERM_MS=200", "-DCLEANUP_MS=1000"]
        self.c(*fixture_flags, SUPERVISOR / "supervisor.c", "-o", self.output / "supervisor.fixture")
        self.c(*fixture_flags, SUPERVISOR / "tests/reap-budget.c", "-o", self.output / "reap-budget.fixture")

    def jpeg(self):
        flags = []
        if self.args.jpeg_include:
            flags += ["-I" + str(self.args.jpeg_include.resolve())]
        if self.args.jpeg_library_dir:
            flags += ["-L" + str(self.args.jpeg_library_dir.resolve())]
        self.c(*self.static, *flags, HERE / "jpeg/nv12_to_jpeg.c", "-ljpeg", "-lm",
               "-o", self.output / "nv12-to-jpeg")

    def aiq(self):
        cflags, cppflags = self.capture_flags(), self.sdk_flags()
        if self.args.host_fixtures:
            self.cpp(*cppflags, HELPER / "aiq_finite.cpp", HELPER / "tests/fake_aiq.cpp",
                     "-o", self.output / "lifecycle.fixture")
            self.c(*cflags, "-DCAPTURE_FIXTURE", HELPER / "camera_diag.c",
                   HELPER / "tests/fixture.c", *C_SOURCES, *WRAPS,
                   "-o", self.output / "capture.fixture")
            sources = [HELPER / "camera_diag.c", HELPER / "tests/fixture.c", *C_SOURCES]
        else:
            sources = [HELPER / "camera_diag.c", *C_SOURCES]
        objects = []
        for index, source in enumerate(sources):
            output = self.output / ("capture-%d.o" % index)
            flags = ["-Dmain=camera_capture_main"] if index == 0 else []
            if self.args.host_fixtures:
                flags += ["-DCAPTURE_FIXTURE"]
            self.c(*cflags, *flags, "-c", source, "-o", output)
            objects.append(output)
        if not self.args.host_fixtures:
            library = self.args.rkaiq_library.resolve()
            self.cpp(*cppflags, HELPER / "aiq_finite.cpp", *objects, library,
                     "-Wl,-rpath-link," + str(library.parent), *WRAPS,
                     "-o", self.output / "aiq-finite")
            return
        self.cpp(*cppflags, "-DAIQ_REAL_CAPTURE", HELPER / "aiq_finite.cpp",
                 HELPER / "tests/fake_aiq.cpp", *objects, *WRAPS,
                 "-o", self.output / "combined.fixture")
        # Keep SDK shared-library I/O outside the executable's syscall wrappers.
        shared = self.output / "libfake-aiq-io.so"
        self.cpp(*cppflags, "-fPIC", "-shared", "-DAIQ_REAL_CAPTURE", "-DAIQ_SHARED_IO_PROBE",
                 HELPER / "tests/fake_aiq.cpp", "-o", shared)
        self.cpp(*cppflags, HELPER / "aiq_finite.cpp", *objects, shared, *WRAPS,
                 "-Wl,-rpath," + str(self.output), "-o", self.output / "shared-io.fixture")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", nargs="+", choices=("launcher", "supervisor", "jpeg", "aiq"),
                        default=["launcher", "supervisor", "jpeg"])
    parser.add_argument("--cross-prefix", default=os.environ.get("CROSS_COMPILE", "aarch64-linux-gnu-"),
                        help="Compiler executable prefix; pass an empty string for native host gcc/g++")
    parser.add_argument("--sysroot", type=Path, default=os.environ.get("SYSROOT"),
                        help="Target sysroot; omitted means the compiler's configured sysroot")
    parser.add_argument("--rkaiq-sdk", type=Path, default=os.environ.get("RKAIQ_SDK"))
    parser.add_argument("--rkaiq-library", type=Path, default=os.environ.get("RKAIQ_LIBRARY"))
    parser.add_argument("--jpeg-include", type=Path)
    parser.add_argument("--jpeg-library-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="Fresh output directory")
    parser.add_argument("--dynamic-utilities", action="store_true",
                        help="Link launcher/supervisor/JPEG dynamically; AIQ is always dynamic")
    parser.add_argument("--host-fixtures", action="store_true",
                        help="Compile only synthetic host fixtures; never execute them")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output must be a new directory")
    if args.sysroot and not args.sysroot.is_dir():
        parser.error("sysroot must be an existing directory")
    if args.host_fixtures and (args.cross_prefix or args.sysroot or "launcher" in args.targets):
        parser.error("host fixtures require --cross-prefix '' without --sysroot or launcher target")
    if "aiq" in args.targets:
        if not args.rkaiq_sdk or not args.rkaiq_sdk.is_dir():
            parser.error("aiq requires an explicit --rkaiq-sdk (or RKAIQ_SDK)")
        if not args.host_fixtures and (not args.rkaiq_library or not args.rkaiq_library.is_file()):
            parser.error("aiq requires an explicit --rkaiq-library (or RKAIQ_LIBRARY)")
    args.output.mkdir(parents=True)
    builder = Builder(args)
    for target in dict.fromkeys(args.targets):
        getattr(builder, target)()
    report = {"commands": builder.commands, "helper_source_sha256": builder.source_sha,
              "helper_sources": builder.sources, "host_fixtures": args.host_fixtures,
              "executed_outputs": False,
              "artifacts": {p.name: digest(p) for p in sorted(builder.output.iterdir()) if p.is_file()}}
    (builder.output / "build-manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Compilation complete; outputs were not executed.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit("Native build failed: " + str(error)) from None
