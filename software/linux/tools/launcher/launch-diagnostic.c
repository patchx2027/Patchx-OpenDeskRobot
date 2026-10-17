#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/magic.h>
#include <net/if.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/sysmacros.h>
#include <sys/utsname.h>
#include <unistd.h>

#define RUNTIME "/dev/rtctrl-patchx-app-v1/runtime"
#define PARENT "/dev/rtctrl-patchx-app-v1"
#define EXPECTED_KERNEL "5.10.160-rt89-g9f9e9d18574d-dirty"

static int refuse(const char *reason) {
    fprintf(stderr, "PatchX diagnostic refused: %s\n", reason);
    return 78;
}

static int owned_directory(int descriptor) {
    struct stat info;
    return fstat(descriptor, &info) == 0 && S_ISDIR(info.st_mode) && info.st_uid == 0 &&
           !(info.st_mode & (S_IWGRP | S_IWOTH));
}

static int directory_at(int parent, const char *name) {
    int descriptor = openat(parent, name, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (descriptor < 0 || !owned_directory(descriptor)) {
        if (descriptor >= 0) {
            close(descriptor);
        }
        return -1;
    }
    return descriptor;
}

static int regular_at(int parent, const char *name, int executable) {
    int descriptor = openat(parent, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    struct stat info;
    int valid = descriptor >= 0 && fstat(descriptor, &info) == 0 && S_ISREG(info.st_mode) &&
                info.st_uid == 0 && !(info.st_mode & (S_IWGRP | S_IWOTH)) &&
                (!executable || (info.st_mode & S_IXUSR));
    if (descriptor >= 0) {
        close(descriptor);
    }
    return valid;
}

static int no_existing_runtime_mounts(void) {
    FILE *stream = fopen("/proc/self/mountinfo", "re");
    if (!stream) {
        return 0;
    }
    char line[8192], point[4096];
    int valid = 1;
    size_t count = 0;
    while (fgets(line, sizeof(line), stream)) {
        if (++count > 8192 || !strchr(line, '\n') ||
            sscanf(line, "%*s %*s %*s %*s %4095s", point) != 1) {
            valid = 0;
            break;
        }
        if (!strncmp(point, PARENT, strlen(PARENT)) &&
            (point[strlen(PARENT)] == '\0' || point[strlen(PARENT)] == '/')) {
            valid = 0;
            break;
        }
    }
    if (ferror(stream)) {
        valid = 0;
    }
    if (fclose(stream)) {
        valid = 0;
    }
    return valid;
}

static int expected_filesystem(const char *path, long magic) {
    int descriptor = open(path, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    struct statfs info;
    int valid = descriptor >= 0 && fstatfs(descriptor, &info) == 0 && info.f_type == magic;
    if (descriptor >= 0 && close(descriptor)) {
        valid = 0;
    }
    return valid;
}

static int loopback_up(void) {
    int descriptor = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (descriptor < 0) {
        return 0;
    }
    struct ifreq request = {0};
    memcpy(request.ifr_name, "lo", 3);
    int valid = ioctl(descriptor, SIOCGIFFLAGS, &request) == 0;
    if (valid) {
        request.ifr_flags |= IFF_UP;
        valid = ioctl(descriptor, SIOCSIFFLAGS, &request) == 0;
    }
    if (close(descriptor)) {
        valid = 0;
    }
    return valid;
}

static int reopen_runtime(int previous) {
    int dev = open("/dev", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    int parent = dev < 0 || !owned_directory(dev) ? -1 :
                 directory_at(dev, "rtctrl-patchx-app-v1");
    int current = parent < 0 ? -1 : directory_at(parent, "runtime");
    struct stat old_info, new_info;
    int valid = current >= 0 && fstat(previous, &old_info) == 0 &&
                fstat(current, &new_info) == 0 && old_info.st_dev == new_info.st_dev &&
                old_info.st_ino == new_info.st_ino;
    if (dev >= 0 && close(dev)) {
        valid = 0;
    }
    if (parent >= 0 && close(parent)) {
        valid = 0;
    }
    if (!valid || close(previous)) {
        if (current >= 0) {
            close(current);
        }
        return -1;
    }
    return current;
}

static int visible_null_device(void) {
    int descriptor = open("/dev/null", O_RDWR | O_CLOEXEC | O_NOFOLLOW);
    struct stat info;
    int valid = descriptor >= 0 && fstat(descriptor, &info) == 0 &&
                S_ISCHR(info.st_mode) && major(info.st_rdev) == 1 && minor(info.st_rdev) == 3;
    if (descriptor >= 0 && close(descriptor)) {
        valid = 0;
    }
    return valid;
}

int main(int argc, char **argv) {
    if (argc != 2 || strcmp(argv[1], "--diagnostic")) {
        return refuse("only the fixed --diagnostic mode exists");
    }
    if (geteuid() != 0) {
        return refuse("root identity is required");
    }
    struct utsname identity;
    if (uname(&identity) || strcmp(identity.sysname, "Linux") ||
        strcmp(identity.release, EXPECTED_KERNEL)) {
        return refuse("kernel identity does not match this finite RAM diagnostic");
    }
    char executable[PATH_MAX];
    ssize_t length = readlink("/proc/self/exe", executable, sizeof(executable) - 1);
    if (length <= 0 || length >= (ssize_t)sizeof(executable) - 1) {
        return refuse("cannot verify executable identity");
    }
    executable[length] = '\0';
    if (strcmp(executable, RUNTIME "/opt/patchx/bin/launch-diagnostic.aarch64")) {
        return refuse("executable is outside the fixed RAM prefix");
    }
    int dev = open("/dev", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (dev < 0) {
        return refuse("cannot verify parent dev directory");
    }
    struct statfs dev_fs, runtime_fs;
    int parent = directory_at(dev, "rtctrl-patchx-app-v1");
    int runtime = parent < 0 ? -1 : directory_at(parent, "runtime");
    if (runtime < 0 || fstatfs(dev, &dev_fs) || fstatfs(runtime, &runtime_fs) ||
        dev_fs.f_type != TMPFS_MAGIC || runtime_fs.f_type != TMPFS_MAGIC ||
        memcmp(&dev_fs.f_fsid, &runtime_fs.f_fsid, sizeof(dev_fs.f_fsid)) ||
        !no_existing_runtime_mounts()) {
        return refuse("RAM prefix is not a private unmounted directory on parent dev tmpfs");
    }
    close(dev);
    close(parent);
    const char *directories[] = {"dev", "proc", "sys", "tmp"};
    for (size_t i = 0; i < sizeof(directories) / sizeof(directories[0]); ++i) {
        int descriptor = directory_at(runtime, directories[i]);
        if (descriptor < 0) {
            return refuse("required runtime directory is missing, writable or a symlink");
        }
        close(descriptor);
    }
    int usr = directory_at(runtime, "usr");
    int usr_bin = usr < 0 ? -1 : directory_at(usr, "bin");
    int opt = directory_at(runtime, "opt");
    int patchx = opt < 0 ? -1 : directory_at(opt, "patchx");
    if (usr_bin < 0 || patchx < 0 || !regular_at(usr_bin, "python3.10", 1) ||
        !regular_at(patchx, "board-application-diagnostic.py", 0)) {
        return refuse("fixed Python or diagnostic source identity is invalid");
    }
    close(usr);
    close(usr_bin);
    close(opt);
    close(patchx);
    if (!expected_filesystem("/proc", PROC_SUPER_MAGIC) ||
        !expected_filesystem("/sys", SYSFS_MAGIC)) {
        return refuse("parent proc/sys source identity is invalid or a symlink");
    }
    struct stat old_mount, old_network;
    if (stat("/proc/self/ns/mnt", &old_mount) || stat("/proc/self/ns/net", &old_network)) {
        return refuse("cannot verify initial namespace identities");
    }
    /* No operation before this point changes namespace, mounts or interfaces. */
    if (unshare(CLONE_NEWNS | CLONE_NEWNET)) {
        return refuse("private mount/network namespaces unavailable; no fallback");
    }
    struct stat new_mount, new_network;
    if (stat("/proc/self/ns/mnt", &new_mount) || stat("/proc/self/ns/net", &new_network) ||
        new_mount.st_ino == old_mount.st_ino || new_network.st_ino == old_network.st_ino) {
        return refuse("new namespace identities were not confirmed");
    }
    if (mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL)) {
        return refuse("cannot make new mount namespace private");
    }
    const char *sources[] = {"/dev", "/proc", "/sys"};
    const char *targets[] = {RUNTIME "/dev", RUNTIME "/proc", RUNTIME "/sys"};
    for (size_t i = 0; i < sizeof(sources) / sizeof(sources[0]); ++i) {
        if (mount(sources[i], targets[i], NULL, MS_BIND, NULL)) {
            return refuse("private nonrecursive device/proc/sys bind failed");
        }
    }
    if (!loopback_up()) {
        return refuse("private diagnostic loopback unavailable; parent interface unchanged");
    }
    /* A descriptor opened before unshare still carries the previous mount tree.
       Reopen the same directory through the current namespace before chroot. */
    runtime = reopen_runtime(runtime);
    if (runtime < 0) {
        return refuse("current namespace runtime identity does not match the original directory");
    }
    if (fchdir(runtime) || chroot(".") || chdir("/")) {
        return refuse("cannot enter fixed bundled userspace");
    }
    close(runtime);
    if (!visible_null_device() || !expected_filesystem("/proc", PROC_SUPER_MAGIC) ||
        !expected_filesystem("/sys", SYSFS_MAGIC)) {
        return refuse("chroot device/proc/sys bindings are not visible; no diagnostic executed");
    }
    if (clearenv() || setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin", 1) ||
        setenv("PYTHONHOME", "/usr", 1) ||
        setenv("PYTHONPATH", "/opt/rtctrl-companion:/usr/lib/python3/dist-packages", 1) ||
        setenv("PYTHONNOUSERSITE", "1", 1) || setenv("PYTHONDONTWRITEBYTECODE", "1", 1) ||
        setenv("PYTHONUNBUFFERED", "1", 1) ||
        setenv("SSL_CERT_FILE", "/etc/ssl/certs/ca-certificates.crt", 1)) {
        return refuse("cannot construct fixed diagnostic environment");
    }
    char *const child[] = {"/usr/bin/python3.10", "-B", "-s",
                           "/opt/patchx/board-application-diagnostic.py", NULL};
    execv(child[0], child);
    return refuse("bundled Python exec failed");
}
