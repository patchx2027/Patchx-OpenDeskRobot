#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build an initramfs that starts the PatchX app from a local ARM64 runtime."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import subprocess
import tarfile
import tempfile

LINUX = Path(__file__).resolve().parents[2] / "software/linux"
DEPLOY = LINUX / "deploy/patchx"


def runtime_digest(archive):
    """Accept a single runtime tree with files, directories and internal symlinks."""
    if archive.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("Compressed runtime exceeds 128 MiB")
    names = {}
    contents_size = 0
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            name = PurePosixPath(member.name)
            if (not name.parts or name.parts[0] != "runtime" or ".." in name.parts
                    or name in names or not (member.isfile() or member.isdir() or member.issym())):
                raise ValueError(f"Unsupported runtime archive member: {member.name}")
            if member.issym():
                target = posixpath.normpath(posixpath.join(str(name.parent), member.linkname))
                if not target.startswith("runtime/"):
                    raise ValueError(f"Runtime link leaves the archive tree: {member.name}")
            contents_size += member.size
            if len(names) >= 16384 or contents_size > 80 * 1024 * 1024:
                raise ValueError("Runtime archive exceeds the RAM deployment limit")
            names[name] = member
        for name in names:
            if any(parent in names and not names[parent].isdir() for parent in name.parents):
                raise ValueError(f"Runtime archive traverses a non-directory: {name}")
        opt = names.get(PurePosixPath("runtime/opt"))
        if opt is None or not opt.isdir():
            raise ValueError("Runtime archive must include an /opt directory")
        if PurePosixPath("runtime/opt/patchx-ram-app") in names:
            raise ValueError("Runtime archive must reserve /opt/patchx-ram-app for deployment")
        for required in ("runtime/bin/sh", "runtime/usr/bin/python3"):
            member = names.get(PurePosixPath(required))
            if member is None or not (member.isfile() or member.issym()):
                raise ValueError(f"Runtime archive is missing {required}")
    return hashlib.sha256(archive.read_bytes()).hexdigest()


def build(args):
    if (not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", args.cache_path)
            or any(part in {".", ".."} for part in args.cache_path.split("/"))):
        raise ValueError("--cache-path must be relative to the board's /cache")
    digest = runtime_digest(args.runtime_archive)
    config = args.config or (LINUX / "apps/patchx/demo.json")
    with config.open(encoding="utf-8") as stream:
        settings = json.load(stream)
    if not isinstance(settings, dict) or settings.get("voice", {}).get("bind", "127.0.0.1") != "127.0.0.1":
        raise ValueError("The RAM app must bind HTTP to 127.0.0.1")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for directory in ("bin", "dev", "etc", "mnt/cache", "opt/patchx-ram-app/deploy/patchx", "proc", "sys", "tmp"):
            (root / directory).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.busybox, root / "bin/busybox")
        (root / "bin/busybox").chmod(0o755)
        (root / "bin/sh").symlink_to("busybox")
        for script in (DEPLOY / "ram").iterdir():
            dest = root / ("init" if script.name == "init" else f"bin/{script.name}")
            shutil.copyfile(script, dest)
            dest.chmod(0o755)
        with (root / "etc/patchx-runtime").open("w", encoding="ascii") as stream:
            stream.write(f"{args.cache_path}\n{digest}\n")
        product = root / "opt/patchx-ram-app"
        for app in ("patchx", "companion"):
            shutil.copytree(LINUX / "apps" / app, product / "apps" / app,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copyfile(config, product / "ram.json")
        shutil.copyfile(DEPLOY / "alsa-rk3568.conf", product / "deploy/patchx/alsa-rk3568.conf")
        subprocess.run([args.strip, "--strip-debug", "-o", root / "snd-soc-rk817.ko", args.codec], check=True)
        if args.battery is not None:
            subprocess.run([args.strip, "--strip-debug", "-o", root / "rk817_battery.ko", args.battery], check=True)
        files = [b".", *(bytes(p.relative_to(root)) for p in sorted(root.rglob("*")))]
        archive = subprocess.run(
            ["cpio", "--quiet", "--null", "-o", "-H", "newc", "--owner", "0:0", "--reproducible"],
            cwd=root, input=b"\0".join(files) + b"\0", stdout=subprocess.PIPE, check=True).stdout
        with args.output.open("xb") as stream:
            stream.write(gzip.compress(archive, compresslevel=9, mtime=0))
    print(f"{args.output}: {args.output.stat().st_size} bytes, runtime SHA256 {digest}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("busybox", "codec", "runtime-archive", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--battery", type=Path, help="Optional RK817 battery module from the same kernel build")
    parser.add_argument("--cache-path", required=True, help="Board path relative to /cache")
    parser.add_argument("--config", type=Path, help="Local PatchX JSON; default is demo")
    parser.add_argument("--strip", default="aarch64-linux-gnu-strip")
    args = parser.parse_args()
    try:
        build(args)
    except (AttributeError, OSError, ValueError, tarfile.TarError, subprocess.SubprocessError) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")


if __name__ == "__main__":
    main()
