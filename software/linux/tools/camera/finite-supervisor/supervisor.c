#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/magic.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/vfs.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#ifndef RUN_MS
#define RUN_MS 30000
#endif
#ifndef TERM_MS
#define TERM_MS 2000
#endif
#ifndef CLEANUP_MS
#define CLEANUP_MS 5000
#endif
#define TERM_FALLBACK_ATTEMPTS ((TERM_MS + 19) / 20)
#define CLEANUP_FALLBACK_ATTEMPTS ((CLEANUP_MS + 19) / 20)
#define MAX_LOG_BYTES (4U * 1024U * 1024U)

static pid_t child_group;
static pid_t main_pid;
static unsigned long long main_start_ticks;
static int main_status;
static int cleanup_error;
static int first_errno;
static const char* first_error = "none";
static bool main_reaped;
static bool identity_verified;
static bool capabilities_verified;
static bool descendants_cleanup_completed;
static bool timed_out;
static unsigned adopted_reaped;
static unsigned term_attempts;
static unsigned kill_attempts;
static volatile sig_atomic_t interrupted;
static int64_t started_ms;

static int64_t now_ms(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now)) {
        return -1;
    }
    return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static void fail_at(const char* phase, int error) {
    if (!strcmp(first_error, "none")) {
        first_error = phase;
        first_errno = error;
    }
}

static void cleanup_fail(int error) {
    if (!cleanup_error) {
        cleanup_error = error;
    }
}

static void record_reap(pid_t pid, int status) {
    if (pid == main_pid) {
        main_reaped = true;
        main_status = status;
    } else {
        ++adopted_reaped;
    }
}

static void record_signal_attempt(int number) {
    if (number == SIGTERM) {
        ++term_attempts;
    } else if (number == SIGKILL) {
        ++kill_attempts;
    }
}

#include "owned-process-cleanup.inc"

static void interrupt_handler(int number) {
    interrupted = number;
}

static int install_signals(void) {
    const int signals[] = {SIGHUP, SIGINT, SIGTERM, SIGALRM};
    struct sigaction action = {.sa_handler = interrupt_handler};
    sigemptyset(&action.sa_mask);
    for (size_t i = 0; i < sizeof(signals) / sizeof(signals[0]); ++i) {
        if (sigaction(signals[i], &action, NULL)) {
            return -1;
        }
    }
    action.sa_handler = SIG_DFL;
    if (sigaction(SIGCHLD, &action, NULL)) {
        return -1;
    }
    action.sa_handler = SIG_IGN;
    return sigaction(SIGPIPE, &action, NULL);
}

static int create_tmpfs_file(const char* path) {
    int fd = open(path,
                  O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK,
                  0600);
    if (fd < 0) {
        return -1;
    }
    struct stat identity;
    struct statfs filesystem;
    if (fstat(fd, &identity) || fstatfs(fd, &filesystem)) {
        int saved = errno;
        close(fd);
        errno = saved;
        return -1;
    }
    if (!S_ISREG(identity.st_mode) || filesystem.f_type != TMPFS_MAGIC ||
        identity.st_size) {
        close(fd);
        errno = EINVAL;
        return -1;
    }
    return fd;
}

static int receipt(int fd, const char* phase) {
    const bool uncertain =
        strcmp(first_error, "none") || cleanup_error || term_attempts ||
        kill_attempts || adopted_reaped ||
        (main_pid && (!main_reaped || !descendants_cleanup_completed));
    char json[2048];
    const int64_t now = now_ms();
    int length = snprintf(
        json,
        sizeof(json),
        "{\"aiq_supervisor\":1,\"phase\":\"%s\",\"first_error\":\"%s\","
        "\"first_errno\":%d,\"cleanup_errno\":%d,\"main_pid\":%ld,"
        "\"main_start_ticks\":%llu,\"identity_verified\":%s,"
        "\"capabilities_verified\":%s,\"main_reaped\":%s,"
        "\"main_status\":%d,\"main_exit\":%d,\"main_signal\":%d,"
        "\"timed_out\":%s,\"term_attempts\":%u,\"kill_attempts\":%u,"
        "\"adopted_reaped\":%u,\"descendants_cleanup_completed\":%s,"
        "\"cleanup_uncertain\":%s,\"physical_cleanup_verified\":false,"
        "\"run_ms\":%d,\"term_ms\":%d,\"cleanup_ms\":%d,\"elapsed_ms\":%lld}\n",
        phase,
        first_error,
        first_errno,
        cleanup_error,
        (long)main_pid,
        main_start_ticks,
        identity_verified ? "true" : "false",
        capabilities_verified ? "true" : "false",
        main_reaped ? "true" : "false",
        main_status,
        main_reaped && WIFEXITED(main_status) ? WEXITSTATUS(main_status) : -1,
        main_reaped && WIFSIGNALED(main_status) ? WTERMSIG(main_status) : 0,
        timed_out ? "true" : "false",
        term_attempts,
        kill_attempts,
        adopted_reaped,
        descendants_cleanup_completed ? "true" : "false",
        uncertain ? "true" : "false",
        RUN_MS,
        TERM_MS,
        CLEANUP_MS,
        (long long)(now >= started_ms ? now - started_ms : -1));
    if (length <= 0 || (size_t)length >= sizeof(json)) {
        errno = EOVERFLOW;
        return -1;
    }
    for (unsigned attempt = 0; attempt < 4; ++attempt) {
        ssize_t result = write(fd, json, (size_t)length);
        if (result == length) {
            return 0;
        }
        if (result < 0 && errno == EINTR) {
            continue;
        }
        if (result >= 0) {
            errno = EIO;
        }
        return -1;
    }
    errno = EINTR;
    return -1;
}

static int verify_child_identity(pid_t pid) {
    struct process_identity before, after;
    if (process_identity(pid, &before) || before.parent != getpid()) {
        errno = ESTALE;
        return -1;
    }
    int fd = (int)syscall(SYS_pidfd_open, pid, 0U);
    if (fd < 0) {
        return -1;
    }
    int failure = 0;
    if (process_identity(pid, &after) || after.parent != getpid() ||
        before.start != after.start) {
        failure = ESTALE;
    } else if (syscall(SYS_pidfd_send_signal, fd, 0, NULL, 0U)) {
        failure = errno;
    }
    if (close(fd) && !failure) {
        failure = errno;
    }
    if (failure) {
        errno = failure;
        return -1;
    }
    main_start_ticks = before.start;
    identity_verified = true;
    return 0;
}

static void child_exec(char** argv, int gate, int output, int errors, pid_t owner) {
    if (setpgid(0, 0) || prctl(PR_SET_PDEATHSIG, SIGKILL) || getppid() != owner) {
        _exit(125);
    }
    const int signals[] = {
        SIGHUP, SIGINT, SIGTERM, SIGALRM, SIGCHLD, SIGPIPE, SIGXFSZ};
    struct sigaction action = {.sa_handler = SIG_DFL};
    sigemptyset(&action.sa_mask);
    for (size_t i = 0; i < sizeof(signals) / sizeof(signals[0]); ++i) {
        if (sigaction(signals[i], &action, NULL)) {
            _exit(125);
        }
    }
    sigset_t empty;
    sigemptyset(&empty);
    if (sigprocmask(SIG_SETMASK, &empty, NULL)) {
        _exit(125);
    }
    char go;
    ssize_t length;
    do {
        length = read(gate, &go, 1);
    } while (length < 0 && errno == EINTR);
    if (length != 1 || go != 'G') {
        _exit(125);
    }
    int null_fd = open("/dev/null", O_RDONLY | O_CLOEXEC);
    if (null_fd < 0 || dup2(null_fd, STDIN_FILENO) < 0 ||
        dup2(output, STDOUT_FILENO) < 0 || dup2(errors, STDERR_FILENO) < 0 ||
        fcntl(STDIN_FILENO, F_SETFD, 0) || fcntl(STDOUT_FILENO, F_SETFD, 0) ||
        fcntl(STDERR_FILENO, F_SETFD, 0) || syscall(SYS_close_range, 3U, ~0U, 0U)) {
        _exit(125);
    }
    struct rlimit log_limit = {.rlim_cur = MAX_LOG_BYTES, .rlim_max = MAX_LOG_BYTES};
    struct rlimit core_limit = {.rlim_cur = 0, .rlim_max = 0};
    if (setrlimit(RLIMIT_FSIZE, &log_limit) || setrlimit(RLIMIT_CORE, &core_limit)) {
        _exit(125);
    }
    char* child_argv[] = {argv[2],
                          "--library-path",
                          argv[3],
                          argv[4],
                          "--capture-once",
                          argv[5],
                          argv[6],
                          argv[7],
                          NULL};
    char* environment[] = {"PATH=/bin:/usr/bin:/sbin",
                           "LANG=C",
                           "LD_BIND_NOW=1",
                           "J2S_NO_CACHE=1",
                           NULL};
    execve(argv[2], child_argv, environment);
    _exit(126);
}

static void run_aiq(char** argv, int output, int errors, int report) {
    int gate[2];
    if (pipe2(gate, O_CLOEXEC)) {
        fail_at("pipe", errno);
        return;
    }
    const int64_t fork_time = now_ms();
    if (fork_time < 0) {
        fail_at("clock", EIO);
        close(gate[0]);
        close(gate[1]);
        return;
    }
    const int64_t deadline = fork_time + RUN_MS;
    const pid_t owner = getpid();
    pid_t pid = fork();
    if (pid < 0) {
        fail_at("fork", errno);
        close(gate[0]);
        close(gate[1]);
        return;
    }
    if (!pid) {
        close(gate[1]);
        child_exec(argv, gate[0], output, errors, owner);
    }
    main_pid = child_group = pid;
    close(gate[0]);
    if (setpgid(pid, pid) && errno != EACCES && errno != ESRCH) {
        fail_at("setpgid", errno);
    } else if (verify_child_identity(pid)) {
        fail_at("child-identity", errno);
    } else if (receipt(report, "started")) {
        fail_at("start-receipt", errno);
    } else if (write(gate[1], "G", 1) != 1) {
        fail_at("child-gate", errno);
    }
    close(gate[1]);
    while (!strcmp(first_error, "none")) {
        int status = 0;
        pid_t result = waitpid(pid, &status, WNOHANG);
        if (result == pid) {
            record_reap(pid, status);
            if (!WIFEXITED(status) || WEXITSTATUS(status)) {
                fail_at("child-failure", ECHILD);
            }
            break;
        }
        if (result < 0 && errno != EINTR) {
            fail_at("waitpid", errno);
            break;
        }
        int64_t current = now_ms();
        if (current < 0) {
            fail_at("clock", EIO);
        } else if (interrupted) {
            fail_at("interrupted", ECANCELED);
        } else if (current >= deadline) {
            timed_out = true;
            fail_at("run-deadline", ETIMEDOUT);
        } else {
            struct timespec delay = {.tv_nsec = 20000000};
            nanosleep(&delay, NULL);
        }
    }
    if (terminate_children()) {
        fail_at("reap-incomplete", cleanup_error);
    }
    if (adopted_reaped) {
        fail_at("unexpected-descendant", ECHILD);
    }
}

int main(int argc, char** argv) {
    const bool probe = argc == 3 && !strcmp(argv[1], "--probe");
    const bool capture = argc == 11 && !strcmp(argv[1], "--capture-once");
    if (!probe && !capture) {
        return 2;
    }
    for (int i = 2; i < argc; ++i) {
        if (argv[i][0] != '/' || strlen(argv[i]) >= PATH_MAX) {
            return 2;
        }
        for (int j = 2; j < i; ++j) {
            if (!strcmp(argv[i], argv[j])) {
                return 2;
            }
        }
    }
    started_ms = now_ms();
    const int report = create_tmpfs_file(argv[probe ? 2 : 10]);
    if (report < 0) {
        return 125;
    }
    int output = -1, errors = -1;
    if (started_ms < 0) {
        fail_at("clock", EIO);
    } else if (install_signals() || prctl(PR_SET_CHILD_SUBREAPER, 1) ||
               process_gate() || syscall(SYS_close_range, ~0U, ~0U, 0U)) {
        fail_at("process-gate", errno);
    } else {
        capabilities_verified = true;
    }
    if (!strcmp(first_error, "none") && capture) {
        output = create_tmpfs_file(argv[8]);
        if (output < 0) {
            fail_at("log-files", errno);
        } else {
            errors = create_tmpfs_file(argv[9]);
            if (errors < 0) {
                fail_at("log-files", errno);
            } else {
                run_aiq(argv, output, errors, report);
            }
        }
    }
    if (output >= 0 && close(output)) {
        cleanup_fail(errno);
    }
    if (errors >= 0 && close(errors)) {
        cleanup_fail(errno);
    }
    if (receipt(report, "final")) {
        cleanup_fail(errno);
    }
    if (close(report)) {
        cleanup_fail(errno);
    }
    if (timed_out) {
        return 124;
    }
    if (strcmp(first_error, "none") || cleanup_error) {
        return main_pid ? 1 : 125;
    }
    return 0;
}
