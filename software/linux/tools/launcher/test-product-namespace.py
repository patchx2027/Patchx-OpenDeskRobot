#!/usr/bin/env python3
"""Real mount namespace and inherited socket tests, using synthetic secrets."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import threading

HERE = Path(__file__).resolve().parent
PRIVATE = HERE.parent / "build/launcher-product-tests"
PREFIX = "/dev/rtctrl-patchx-product-host-fixture"
ROOT = PREFIX + "/runtime"
KEYS = ("RTCTRL_VOICE_TOKEN", "PATCHX_MQTT_USERNAME", "PATCHX_MQTT_PASSWORD", "PATCHX_MQTT_CLIENT_ID")
PROBE_C = r'''
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <fcntl.h>
#include <linux/magic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/sysmacros.h>
#include <sys/time.h>
#include <unistd.h>
extern char **environ;
int main(int argc, char **argv) {
    struct stat node, mount, net, parent_mount, parent_net;
    struct statfs proc, sys;
    char path[128];
    int null_ok = !lstat("/dev/null", &node) && S_ISCHR(node.st_mode) &&
                  major(node.st_rdev) == 1 && minor(node.st_rdev) == 3;
    int fd = open("/dev/null", O_RDWR | O_CLOEXEC | O_NOFOLLOW);
    null_ok = null_ok && fd >= 0;
    if (fd >= 0) close(fd);
    int fs_ok = !statfs("/proc", &proc) && proc.f_type == PROC_SUPER_MAGIC &&
                !statfs("/sys", &sys) && sys.f_type == SYSFS_MAGIC;
    snprintf(path, sizeof(path), "/proc/%ld/ns/mnt", (long)getppid());
    int mount_ok = !stat("/proc/self/ns/mnt", &mount) && !stat(path, &parent_mount) &&
                   mount.st_ino != parent_mount.st_ino;
    snprintf(path, sizeof(path), "/proc/%ld/ns/net", (long)getppid());
    int net_ok = !stat("/proc/self/ns/net", &net) && !stat(path, &parent_net) &&
                 net.st_ino == parent_net.st_ino;
    int args_ok = argc == 6 && !strcmp(argv[0], "/usr/bin/python3.10") &&
                  !strcmp(argv[1], "-B") && !strcmp(argv[2], "-m") &&
                  !strcmp(argv[3], "apps.patchx") && !strcmp(argv[4], "--config") &&
                  !strcmp(argv[5], "/opt/patchx/live.json");
    const char *keys[] = {"RTCTRL_VOICE_TOKEN", "PATCHX_MQTT_USERNAME", "PATCHX_MQTT_PASSWORD", "PATCHX_MQTT_CLIENT_ID"};
    char mode[64] = "default";
    FILE *mode_file = fopen("/opt/patchx/probe-mode", "r");
    if (mode_file) {
        if (!fgets(mode, sizeof(mode), mode_file)) mode[0] = '\0';
        fclose(mode_file);
        mode[strcspn(mode, "\n")] = '\0';
    }
    int credentials_ok = 1;
    size_t expected_env_count = 13;
    for (size_t i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i) {
        const char *value = getenv(keys[i]);
        char expected_mode[64];
        snprintf(expected_mode, sizeof(expected_mode), "max:%zu", i);
        if (!strcmp(mode, expected_mode)) {
            if (!value || strlen(value) != 8192 || strspn(value, "x") != 8192) credentials_ok = 0;
            continue;
        }
        snprintf(expected_mode, sizeof(expected_mode), "empty:%zu", i);
        if (!strcmp(mode, expected_mode)) {
            if (!value || value[0]) credentials_ok = 0;
            continue;
        }
        snprintf(expected_mode, sizeof(expected_mode), "missing:%zu", i);
        if (!strcmp(mode, expected_mode)) {
            if (value) credentials_ok = 0;
            expected_env_count = 12;
            continue;
        }
        if (!value || strcmp(value, "product-synthetic-fixture")) credentials_ok = 0;
    }
    const char *fixed[][2] = {
        {"PATH", "/usr/bin:/bin:/usr/sbin:/sbin"}, {"PYTHONHOME", "/usr"},
        {"PYTHONPATH", "/opt/rtctrl-companion:/usr/lib/python3/dist-packages"},
        {"PYTHONNOUSERSITE", "1"}, {"PYTHONDONTWRITEBYTECODE", "1"},
        {"PYTHONUNBUFFERED", "1"}, {"ALSA_CONFIG_PATH", "/opt/patchx/alsa-rk3568.conf"},
        {"SSL_CERT_FILE", "/etc/ssl/certs/ca-certificates.crt"}, {"SSL_CERT_DIR", "/etc/ssl/certs"}
    };
    int env_ok = 1;
    for (size_t i = 0; i < sizeof(fixed) / sizeof(fixed[0]); ++i) {
        const char *value = getenv(fixed[i][0]);
        if (!value || strcmp(value, fixed[i][1])) env_ok = 0;
    }
    size_t env_count = 0;
    for (char **item = environ; *item; ++item) ++env_count;
    env_ok = env_ok && env_count == expected_env_count;
    int socket_ok = 0;
    fd = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd >= 0) {
        struct timeval limit = {0, 500000};
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &limit, sizeof(limit));
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &limit, sizeof(limit));
        struct sockaddr_in address = {.sin_family = AF_INET, .sin_port = htons(PROBE_PORT)};
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        const char request[] = "PATCHX_PRODUCT_NETWORK_PROBE\n";
        char reply[4] = {0};
        if (!connect(fd, (struct sockaddr *)&address, sizeof(address)) &&
            send(fd, request, sizeof(request) - 1, 0) == (ssize_t)sizeof(request) - 1 &&
            recv(fd, reply, sizeof(reply) - 1, MSG_WAITALL) == 3 && !memcmp(reply, "OK\n", 3)) socket_ok = 1;
        close(fd);
    }
    printf("{\"dev_null\":%s,\"proc_sys\":%s,\"new_mount_ns\":%s,\"same_network_ns\":%s,"
           "\"fixed_args\":%s,\"credentials_preserved\":%s,\"environment_allowlist\":%s,\"inherited_socket\":%s}\n",
           null_ok ? "true" : "false", fs_ok ? "true" : "false", mount_ok ? "true" : "false",
           net_ok ? "true" : "false", args_ok ? "true" : "false", credentials_ok ? "true" : "false",
           env_ok ? "true" : "false", socket_ok ? "true" : "false");
    return null_ok && fs_ok && mount_ok && net_ok && args_ok && credentials_ok && env_ok && socket_ok ? 0 : 3;
}
'''


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def driver(phase, launcher, probe):
    subprocess.run(["mount", "--make-rprivate", "/"], check=True)
    subprocess.run(["mount", "-t", "tmpfs", "-o", "size=16m,mode=755", "none", "/dev"], check=True)
    os.mknod("/dev/null", stat.S_IFCHR | 0o666, os.makedev(1, 3))
    root = Path(ROOT)
    for relative in ("dev", "proc", "sys", "tmp", "usr/bin", "opt/patchx/bin"):
        (root / relative).mkdir(parents=True, mode=0o755, exist_ok=True)
    name = "launch-diagnostic.aarch64" if phase == "red" else "launch-product.aarch64"
    installed = root / "opt/patchx/bin" / name
    shutil.copy2(launcher, installed)
    shutil.copy2(probe, root / "usr/bin/python3.10")
    (root / "opt/patchx/board-application-diagnostic.py").write_text("# static host probe\n")
    live = root / "opt/patchx/live.json"
    live.write_text("{}\n")
    live.chmod(0o600)
    (root / "opt/patchx/alsa-rk3568.conf").write_text("# host probe placeholder\n")
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "UNEXPECTED_PRODUCT_ENV": "must-disappear",
           "PYTHONINSPECT": "1", "HTTP_PROXY": "must-disappear"}
    env.update({key: "product-synthetic-fixture" for key in KEYS})
    def execute(args, overrides=None, absent=None, unprivileged=False):
        copied_env = dict(env)
        if overrides:
            copied_env.update(overrides)
        if absent:
            del copied_env[absent]
        def drop_identity():
            os.setgroups([])
            os.setgid(1000)
            os.setuid(1000)
        result = subprocess.run([str(installed)] + args, env=copied_env, capture_output=True, timeout=5,
                                preexec_fn=drop_identity if unprivileged else None)
        return {"exit": result.returncode, "stdout": result.stdout.decode(), "stderr": result.stderr.decode()}
    parent_before = sha("/proc/self/mountinfo")
    ns_before = {key: os.readlink("/proc/self/ns/" + key) for key in ("mnt", "net")}
    cases = {}
    if phase == "red":
        cases["old_product_cli"] = execute(["--product"])
        cases["old_isolated_network"] = execute(["--diagnostic"])
    else:
        cases["product"] = execute(["--product"])
        mode_file = root / "opt/patchx/probe-mode"
        for index, key in enumerate(KEYS):
            mode_file.write_text("max:" + str(index) + "\n")
            cases["accepted_max_" + key] = execute(["--product"], {key: "x" * 8192})
            mode_file.write_text("empty:" + str(index) + "\n")
            cases["accepted_empty_" + key] = execute(["--product"], {key: ""})
            mode_file.write_text("missing:" + str(index) + "\n")
            cases["accepted_missing_" + key] = execute(["--product"], absent=key)
        mode_file.unlink()
        for label, args in (("missing_cli", []), ("wrong_cli", ["--diagnostic"]), ("extra_cli", ["--product", "extra"])):
            cases[label] = execute(args)
        cases["nonroot_identity"] = execute(["--product"], unprivileged=True)
        for key in KEYS:
            cases["oversized_" + key] = execute(["--product"], {key: "x" * 8193})
        live.chmod(0o666)
        cases["writable_config"] = execute(["--product"])
        live.chmod(0o600)
        os.chown(live, 1000, 1000)
        cases["unowned_config"] = execute(["--product"])
        os.chown(live, 0, 0)
        live.rename(live.with_suffix(".real"))
        live.symlink_to("live.real")
        cases["symlink_config"] = execute(["--product"])
        live.unlink()
        live.with_suffix(".real").rename(live)
        python = root / "usr/bin/python3.10"
        python.rename(python.with_suffix(".real"))
        python.symlink_to("python3.real")
        cases["symlink_python"] = execute(["--product"])
        python.unlink()
        python.with_suffix(".real").rename(python)
        sysdir = root / "sys"
        sysdir.chmod(0o777)
        cases["writable_runtime_sys"] = execute(["--product"])
        sysdir.chmod(0o755)
        devdir = root / "dev"
        devdir.rename(root / "dev-real")
        devdir.symlink_to("dev-real", target_is_directory=True)
        cases["symlink_runtime_dev"] = execute(["--product"])
        devdir.unlink()
        (root / "dev-real").rename(devdir)
        installed.chmod(0o777)
        cases["writable_launcher"] = execute(["--product"])
        installed.chmod(0o755)
        subprocess.run(["mount", "--bind", "/sys", str(sysdir)], check=True)
        try:
            cases["existing_runtime_mount"] = execute(["--product"])
        finally:
            subprocess.run(["umount", str(sysdir)], check=True)
    print(json.dumps({"cases": cases, "parent_mountinfo_unchanged": parent_before == sha("/proc/self/mountinfo"),
                      "parent_namespaces_unchanged": ns_before == {key: os.readlink("/proc/self/ns/" + key) for key in ("mnt", "net")}}))


def run(phase, label):
    if os.geteuid() != 0:
        raise RuntimeError("real root mount namespace testing is required; no fallback")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", label):
        raise ValueError("simple unique label required")
    PRIVATE.mkdir(parents=True, exist_ok=True)
    work = PRIVATE / label
    work.mkdir(exist_ok=False)
    production = HERE / ("launch-diagnostic.c" if phase == "red" else "launch-product.c")
    source = work / "launcher-test-copy.c"
    source.write_text(production.read_text().replace("rtctrl-patchx-app-v1", Path(PREFIX).name).replace(
        "5.10.160-rt89-g9f9e9d18574d-dirty", os.uname().release))
    received = []
    listener = socket.socket()
    listener.settimeout(0.5)
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    stop = threading.Event()
    def serve():
        while not stop.is_set():
            try:
                peer, _ = listener.accept()
            except socket.timeout:
                continue
            with peer:
                peer.settimeout(1)
                request = peer.recv(64)
                if request == b"PATCHX_PRODUCT_NETWORK_PROBE\n":
                    received.append(True)
                    peer.sendall(b"OK\n")
    server = threading.Thread(target=serve, daemon=True)
    server.start()
    probe_source = work / "python-probe.c"
    probe_source.write_text(PROBE_C.replace("PROBE_PORT", str(listener.getsockname()[1])))
    launcher, probe = work / "launcher-host", work / "python-probe"
    builds = []
    for original, destination in ((source, launcher), (probe_source, probe)):
        argv = ["gcc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-static", str(original), "-o", str(destination)]
        result = subprocess.run(argv, capture_output=True, timeout=20)
        (work / (destination.name + "-compile.stdout")).write_bytes(result.stdout)
        (work / (destination.name + "-compile.stderr")).write_bytes(result.stderr)
        builds.append({"argv": argv, "exit": result.returncode})
        if result.returncode:
            raise RuntimeError("static host fixture compilation failed")
    parent_ns = {key: os.readlink("/proc/self/ns/" + key) for key in ("mnt", "net")}
    parent_mounts = sha("/proc/self/mountinfo")
    parent_path = Path(PREFIX).exists()
    try:
        argv = ["unshare", "-m", "--", sys.executable, str(Path(__file__).resolve()), "--driver", phase,
                str(launcher.resolve()), str(probe.resolve())]
        result = subprocess.run(argv, capture_output=True, timeout=20)
        (work / "driver.stdout").write_bytes(result.stdout)
        (work / "driver.stderr").write_bytes(result.stderr)
    finally:
        stop.set()
        server.join(timeout=2)
        listener.close()
    if result.returncode or result.stderr:
        raise RuntimeError("actual namespace driver failed")
    evidence = json.loads(result.stdout)
    cases = evidence["cases"]
    if phase == "red":
        old = json.loads(cases["old_isolated_network"]["stdout"])
        if cases["old_product_cli"]["exit"] != 78 or cases["old_isolated_network"]["exit"] != 3:
            raise RuntimeError("old launcher did not reproduce missing product behavior")
        if any(old[key] for key in ("same_network_ns", "credentials_preserved", "inherited_socket")):
            raise RuntimeError("old launcher unexpectedly preserved network or credentials")
    else:
        green = json.loads(cases["product"]["stdout"])
        if cases["product"]["exit"] != 0 or not all(green.values()) or not received:
            raise RuntimeError("production launcher failed inherited socket/namespace/environment proof")
        for label, case in cases.items():
            if label == "product" or label.startswith("accepted_"):
                target = json.loads(case["stdout"])
                if case["exit"] != 0 or not all(target.values()) or case["stderr"]:
                    raise RuntimeError("accepted bounded product input failed: " + label)
            elif case["exit"] != 78 or case["stdout"]:
                raise RuntimeError("bad product input was not refused: " + label)
    if (not evidence["parent_mountinfo_unchanged"] or not evidence["parent_namespaces_unchanged"] or
            parent_mounts != sha("/proc/self/mountinfo") or parent_ns != {key: os.readlink("/proc/self/ns/" + key) for key in ("mnt", "net")} or
            parent_path != Path(PREFIX).exists()):
        raise RuntimeError("test changed parent mount/namespace/path state")
    evidence.update(schema_version=1, phase=phase, production_source_sha256=sha(production),
        test_source_sha256=sha(source), probe_sha256=sha(probe), builds=builds,
        actual_mount_namespace=True, production_preserves_network_namespace=phase == "green",
        actual_parent_socket_responses=len(received), original_parent_unchanged=True,
        secret_keys_only=list(KEYS), secret_values_logged=False, hardware_accessed=False,
        test_copy_only_changes=["fixed runtime prefix", "host kernel identity"],
        limits="Static host probe substitutes Python; no board/device/cloud/real credential access")
    save(work / "result.json", evidence)
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("red", "green"))
    parser.add_argument("--label")
    parser.add_argument("--driver", nargs=3)
    options = parser.parse_args()
    if options.driver:
        driver(*options.driver)
    elif options.phase:
        print(json.dumps(run(options.phase, options.label or options.phase), indent=2))
    else:
        parser.error("phase or internal driver required")
