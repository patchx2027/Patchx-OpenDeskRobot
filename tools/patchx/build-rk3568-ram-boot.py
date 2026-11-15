#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Assemble the old 3568A RAM-test image from user-supplied inputs."""

import argparse
import gzip
import hashlib
import io
from pathlib import Path
import re
import stat
import struct
import subprocess
import tempfile
import zlib


PAGE = 2048
PACKAGE_SIZE = 40 * 1024 * 1024
BOOT_SHA = "0db7ae12eebf0f3819e5fb72d9291abb4f12222084d87348a6a95a6a00f21b28"
DTBO_SHA = "59b971b4300092de56164904d6a1747276eb569acfc31c09dda6569aa5c8e57d"
CMDLINE = b"console=ttyFIQ0 earlycon=uart8250,mmio32,0xfe660000 rdinit=/init ro loglevel=7 panic=0"
CODEC_NODE = "/i2c@fdd40000/pmic@20/codec"
AMP_PROPERTY = "rockchip,hp-no-mic-uses-spk-ctl"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def aligned(size, block):
    return (size + block - 1) // block * block


def pad(data, block):
    return data.ljust(aligned(len(data), block), b"\0")


def read_input(path, limit):
    require(stat.S_ISREG(path.stat().st_mode), f"{path}: expected a regular file")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    require(0 < len(data) <= limit, f"{path}: empty or exceeds {limit} bytes")
    return data


def locked_input(path, size, digest):
    data = read_input(path, size)
    require(len(data) == size and hashlib.sha256(data).hexdigest() == digest,
            f"{path}: expected the verified old 3568A stock partition backup")
    return data


def validate_kernel(kernel, release, initrd_size):
    require(kernel[56:60] == b"ARM\x64" and kernel[32:56] == bytes(24),
            "Expected an uncompressed ARM64 Image")
    offset, extent, flags = struct.unpack_from("<QQQ", kernel, 8)
    require(offset == 0 and flags == 0xA and len(kernel) <= extent <= 0x03C00000,
            "Image header or size differs from the verified RAM load plan")
    require(0x04000000 + initrd_size <= 0x08400000, "Initramfs leaves the first RAM bank")
    require(b"Linux version " + release + b" " in kernel,
            "Image kernel banner does not match --kernel-release")


def validate_initramfs(data, release):
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        archive = stream.read(32 * 1024 * 1024 + 1)
    require(len(archive) <= 32 * 1024 * 1024, "Initramfs expands beyond 32 MiB")
    cursor = 0
    seen_init = False
    seen_codec = False
    names = set()
    while cursor + 110 <= len(archive):
        header = archive[cursor:cursor + 110]
        require(header[:6] == b"070701", "Expected gzip-compressed newc initramfs")
        fields = (header[14:22], header[54:62], header[94:102])
        require(all(re.fullmatch(rb"[0-9a-fA-F]{8}", field) for field in fields),
                "Malformed initramfs member fields")
        mode, size, name_size = (int(field, 16) for field in fields)
        name_end = cursor + 110 + name_size
        start = aligned(name_end, 4)
        require(name_size > 1 and start + size <= len(archive) and archive[name_end - 1] == 0,
                "Malformed initramfs member")
        name = archive[cursor + 110:name_end - 1].removeprefix(b"./")
        if name == b"TRAILER!!!":
            require(size == 0 and not any(archive[start:]), "Unexpected data after initramfs trailer")
            require(seen_init and seen_codec, "Initramfs needs executable /init and matching RK817 module")
            return
        require(name not in names, "Duplicate initramfs member")
        names.add(name)
        regular = mode & 0o170000 == 0o100000
        if name == b"init":
            seen_init = regular and bool(mode & 0o111) and size > 0
        if name.rsplit(b"/", 1)[-1] == b"snd-soc-rk817.ko":
            module = archive[start:start + size]
            require(regular and module[:6] == b"\x7fELF\x02\x01" and module[18:20] == b"\xb7\0"
                    and b"\0vermagic=" + release + b" " in module,
                    "RK817 module does not match ARM64 Image release")
            seen_codec = True
        cursor = aligned(start + size, 4)
    raise ValueError("Missing initramfs trailer")


def check_overlay(dtb, stock_dtbo):
    # Stock partition identity is locked before reading its sole entry.
    require(dtb[:4] == b"\xd0\x0d\xfe\xed" and len(dtb) >= 40
            and struct.unpack_from(">I", dtb, 4)[0] == len(dtb),
            "Expected a complete product DTB")
    reserve = struct.unpack_from(">I", dtb, 16)[0]
    require(reserve + 48 <= len(dtb) and struct.unpack_from(">6Q", dtb, reserve) ==
            (0x0A100000, 0x25000, 0x0A200000, 0xC8C20, 0, 0),
            "Unexpected DTB memory reservations")
    with tempfile.TemporaryDirectory() as directory:
        base, overlay, applied = (Path(directory) / name for name in ("base.dtb", "stock.dtbo", "applied.dtb"))
        base.write_bytes(dtb)
        overlay.write_bytes(stock_dtbo[64:623])
        chosen = subprocess.check_output(["fdtget", "-p", base, "/chosen"], text=True)
        require("bootargs_ext" not in chosen.splitlines(), "Expected a pre-overlay product DTB")
        subprocess.run(["fdtoverlay", "-i", base, "-o", applied, overlay], check=True)
        require(applied.stat().st_size <= 0x40000, "Applied DTB exceeds loader workspace")
        for path in (base, applied):
            value = subprocess.check_output(["fdtget", "-t", "bx", path, CODEC_NODE, AMP_PROPERTY])
            require(not value.strip(), "Expected a boolean codec amplifier property")
        devices = subprocess.check_output(
            ["fdtget", "-t", "s", applied, "/chosen", "bootargs_ext"], text=True)
        require(devices.strip() == "androidboot.boot_devices=fe310000.sdhci,fe330000.nandc",
                "Unexpected old-board overlay boot device list")


def replace_resource_dtbs(stock, dtb):
    kernel_size, _, ramdisk_size, _, resource_size = struct.unpack_from("<5I", stock, 8)
    start = PAGE + aligned(kernel_size, PAGE) + aligned(ramdisk_size, PAGE)
    original = stock[start:start + resource_size]
    # The locked stock image contains nine DTBs and two logos. Keep the entry order.
    count = struct.unpack_from("<I", original, 12)[0]
    resource = bytearray(original[:512 * (1 + count)])
    for index in range(count):
        entry = 512 * (1 + index)
        name = original[entry + 4:entry + 224].split(b"\0", 1)[0]
        _, block, size = struct.unpack_from("<III", original, entry + 256)
        data = dtb if name.endswith(b".dtb") else original[block * 512:block * 512 + size]
        resource[entry + 224:entry + 244] = hashlib.sha1(data).digest()
        struct.pack_into("<III", resource, entry + 256, 20, len(resource) // 512, len(data))
        resource.extend(pad(data, 512))
    return resource


def pack(stock, kernel, initrd, resource, dtb):
    # Android boot v2 header offsets. Keep the tested stock address and OS fields.
    header = bytearray(stock[:PAGE])
    for offset, data in zip((8, 16, 24, 1648), (kernel, initrd, resource, dtb)):
        struct.pack_into("<I", header, offset, len(data))
    header[64:576] = CMDLINE.ljust(512, b"\0")
    header[608:1632] = bytes(1024)
    struct.pack_into("<IQ", header, 1632, 0, 0)
    digest = hashlib.sha1()
    for data in (kernel, initrd, resource, b"", dtb):
        digest.update(data)
        digest.update(struct.pack("<I", len(data)))
    header[576:608] = digest.digest().ljust(32, b"\0")
    image = b"".join((header, *(pad(data, PAGE) for data in (kernel, initrd, resource, dtb))))
    require(len(image) <= PACKAGE_SIZE, "Package exceeds the verified 40 MiB load window")
    return image.ljust(PACKAGE_SIZE, b"\0")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("stock-boot", "stock-dtbo", "image", "dtb", "initramfs", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--kernel-release", required=True, help="Value in include/config/kernel.release")
    args = parser.parse_args()
    try:
        require(re.fullmatch(r"5\.10\.160-rt89[A-Za-z0-9._+-]*", args.kernel_release),
                "Expected the PatchX 5.10.160-rt89 release")
        release = args.kernel_release.encode("ascii")
        stock = locked_input(args.stock_boot, PACKAGE_SIZE, BOOT_SHA)
        overlay = locked_input(args.stock_dtbo, 4 * 1024 * 1024, DTBO_SHA)
        kernel = read_input(args.image, PACKAGE_SIZE)
        dtb = read_input(args.dtb, 0x40000)
        initrd = read_input(args.initramfs, PACKAGE_SIZE)
        validate_kernel(kernel, release, len(initrd))
        validate_initramfs(initrd, release)
        check_overlay(dtb, overlay)
        image = pack(stock, kernel, initrd, replace_resource_dtbs(stock, dtb), dtb)
        with args.output.open("xb") as output:
            output.write(image)
    except (OSError, ValueError, EOFError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")
    print(f"{args.output}: {len(image)} bytes, CRC32 {zlib.crc32(image):08x}, "
          f"SHA256 {hashlib.sha256(image).hexdigest()}")


if __name__ == "__main__":
    main()
