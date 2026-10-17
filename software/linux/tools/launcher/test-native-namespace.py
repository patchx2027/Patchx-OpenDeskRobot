#!/usr/bin/env python3
"""Real private mount/net namespace regression; no test binaries are packaged."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import re

HERE = Path(__file__).resolve().parent
PRIVATE = HERE.parent / "build/launcher-diagnostic-tests"
FIXTURE_PARENT = "/dev/rtctrl-patchx-host-fixture"
FIXTURE_ROOT = FIXTURE_PARENT + "/runtime"
PROBE_C = r'''
#define _GNU_SOURCE
#include <fcntl.h>
#include <linux/magic.h>
#include <net/if.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/sysmacros.h>
#include <unistd.h>
int main(int argc, char **argv) {
    struct stat node;
    struct statfs proc, sys;
    int null_ok = !lstat("/dev/null", &node) && S_ISCHR(node.st_mode) &&
                  major(node.st_rdev) == 1 && minor(node.st_rdev) == 3;
    int descriptor = open("/dev/null", O_RDWR | O_CLOEXEC | O_NOFOLLOW);
    int open_ok = descriptor >= 0;
    if (descriptor >= 0) close(descriptor);
    int proc_ok = !statfs("/proc", &proc) && proc.f_type == PROC_SUPER_MAGIC;
    int sys_ok = !statfs("/sys", &sys) && sys.f_type == SYSFS_MAGIC;
    int args_ok = argc == 4 && !strcmp(argv[1], "-B") && !strcmp(argv[2], "-s") &&
                  !strcmp(argv[3], "/opt/patchx/board-application-diagnostic.py");
    int env_ok = getenv("PYTHONHOME") && !strcmp(getenv("PYTHONHOME"), "/usr") &&
                 getenv("PYTHONPATH") && !strcmp(getenv("PYTHONPATH"),
                 "/opt/rtctrl-companion:/usr/lib/python3/dist-packages");
    struct ifreq request = {0};
    memcpy(request.ifr_name, "lo", 3);
    descriptor = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    int lo_ok = descriptor >= 0 && !ioctl(descriptor, SIOCGIFFLAGS, &request) &&
                (request.ifr_flags & IFF_UP);
    if (descriptor >= 0) close(descriptor);
    printf("{\"dev_null\":%s,\"open_dev_null\":%s,\"proc\":%s,\"sys\":%s,"
           "\"fixed_args\":%s,\"fixed_env\":%s,\"private_lo_up\":%s}\n",
           null_ok ? "true" : "false", open_ok ? "true" : "false",
           proc_ok ? "true" : "false", sys_ok ? "true" : "false",
           args_ok ? "true" : "false", env_ok ? "true" : "false",
           lo_ok ? "true" : "false");
    return null_ok && open_ok && proc_ok && sys_ok && args_ok && env_ok && lo_ok ? 0 : 3;
}
'''


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def driver(launcher, probe):
    # This driver itself already runs in fresh namespaces. A new /dev tmpfs
    # ensures no fixture directories or nodes appear in the parent namespace.
    subprocess.run(["mount", "--make-rprivate", "/"], check=True)
    subprocess.run(["mount", "-t", "tmpfs", "-o", "size=16m,mode=755", "none", "/dev"], check=True)
    os.mknod("/dev/null", stat.S_IFCHR | 0o666, os.makedev(1, 3))
    root = Path(FIXTURE_ROOT)
    for relative in ("dev", "proc", "sys", "tmp", "usr/bin", "opt/patchx/bin"):
        (root / relative).mkdir(parents=True, mode=0o755, exist_ok=True)
    shutil.copy2(launcher, root / "opt/patchx/bin/launch-diagnostic.aarch64")
    shutil.copy2(probe, root / "usr/bin/python3.10")
    (root / "opt/patchx/board-application-diagnostic.py").write_text("# static host fixture probe\n")
    result = subprocess.run([str(root / "opt/patchx/bin/launch-diagnostic.aarch64"), "--diagnostic"],
                            capture_output=True, timeout=5)
    print(json.dumps({"exit": result.returncode, "stdout": result.stdout.decode(),
                      "stderr": result.stderr.decode(),
                      "directories": {path: {"mode": oct(os.lstat(path).st_mode), "uid": os.lstat(path).st_uid}
                          for path in ("/dev", FIXTURE_PARENT, FIXTURE_ROOT)},
                      "fs": subprocess.check_output(["stat", "-f", "-c", "%t %i", "/dev", FIXTURE_ROOT]).decode(),
                      "mountinfo_max_line": max(len(line) for line in Path("/proc/self/mountinfo").read_bytes().splitlines())}))


def run(phase, label):
    if os.geteuid() != 0:
        raise RuntimeError("Run the host fixture as root; no namespace fallback exists")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", label):
        raise ValueError("Fixture label must be a simple lowercase name")
    PRIVATE.mkdir(parents=True, exist_ok=True)
    work = PRIVATE / ("runtime-namespace-" + label)
    work.mkdir(exist_ok=False)
    production = HERE / "launch-diagnostic.c"
    original = production.read_text()
    replaced = original
    for old, new in (("rtctrl-patchx-app-v1", Path(FIXTURE_PARENT).name),
                     ("5.10.160-rt89-g9f9e9d18574d-dirty", os.uname().release)):
        replaced = replaced.replace(old, new)
    source = work / "launcher-test-copy.c"
    source.write_text(replaced)
    probe_source = work / "fixed-python-probe.c"
    probe_source.write_text(PROBE_C)
    launcher, probe = work / "launcher-host", work / "fixed-python-probe"
    for source_path, output in ((source, launcher), (probe_source, probe)):
        subprocess.run(["gcc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-static",
                        str(source_path), "-o", str(output)], check=True, capture_output=True, timeout=20)
    before_ns = {name: os.readlink("/proc/self/ns/" + name) for name in ("mnt", "net")}
    before_mounts = digest("/proc/self/mountinfo")
    before_parent_exists = Path(FIXTURE_PARENT).exists()
    executed = subprocess.run(["unshare", "-m", "-n", "--", sys.executable, str(Path(__file__).resolve()),
                              "--driver", str(launcher.resolve()), str(probe.resolve())],
                             capture_output=True, timeout=10)
    (work / "driver.stdout").write_bytes(executed.stdout)
    (work / "driver.stderr").write_bytes(executed.stderr)
    if executed.returncode or executed.stderr:
        raise RuntimeError("Real private namespace fixture could not run; no fallback")
    result = json.loads(executed.stdout)
    if not result["stdout"]:
        raise RuntimeError("Launcher refused before the regression probe: " + json.dumps(result))
    target = json.loads(result["stdout"])
    if phase == "red":
        if result["exit"] != 3 or any(target[key] for key in ("dev_null", "open_dev_null", "proc", "sys")):
            raise RuntimeError("Original launcher did not reproduce its invisible-bind failure")
    elif result["exit"] != 0 or not all(target.values()) or result["stderr"]:
        raise RuntimeError("Fixed launcher did not enter the new namespace mount tree")
    after_ns = {name: os.readlink("/proc/self/ns/" + name) for name in ("mnt", "net")}
    if before_ns != after_ns or before_mounts != digest("/proc/self/mountinfo") or before_parent_exists != Path(FIXTURE_PARENT).exists():
        raise RuntimeError("Namespace fixture changed parent state")
    result.update(phase=phase, actual_private_mount_and_net_namespaces=True,
        production_source_sha256=digest(production), test_source_sha256=digest(source),
        host_probe_sha256=digest(probe), parent_namespaces_unchanged=True,
        parent_mountinfo_unchanged=True, parent_fixture_path_unchanged=True,
        test_copy_only_changes=["fixed prefix", "host kernel identity"],
        limits="Static host probe substitutes Python; no board, ALSA, ISP or cloud access")
    (work / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("green",))
    parser.add_argument("--label")
    parser.add_argument("--driver", nargs=2)
    args = parser.parse_args()
    if args.driver:
        driver(*args.driver)
    elif args.phase:
        print(json.dumps(run(args.phase, args.label or args.phase), indent=2))
    else:
        parser.error("phase or internal driver required")
