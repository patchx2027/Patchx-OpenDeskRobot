struct process_identity {
    pid_t parent;
    unsigned long long start;
};

/* Return 1 only for an explicitly vanished /proc entry; all other failures stay
 * errors. */
static int process_identity(pid_t pid, struct process_identity* identity) {
    char path[96], content[4096];
    snprintf(path, sizeof(path), "/proc/%ld/stat", (long)pid);
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return errno == ENOENT ? 1 : -1;
    ssize_t length = read(fd, content, sizeof(content) - 1);
    int saved = errno;
    if (close(fd))
        return -1;
    if (length < 0) {
        errno = saved;
        return -1;
    }
    if (length == (ssize_t)sizeof(content) - 1) {
        errno = EOVERFLOW;
        return -1;
    }
    content[length] = 0;
    char* end;
    errno = 0;
    long reported_pid = strtol(content, &end, 10);
    char* comm_end = strrchr(content, ')');
    if (errno || reported_pid != pid || strncmp(end, " (", 2) || !comm_end ||
        comm_end[1] != ' ') {
        errno = EPROTO;
        return -1;
    }
    char* save = NULL;
    char* token = strtok_r(comm_end + 2, " \n", &save);
    for (unsigned field = 3; field <= 22; ++field) {
        if (!token) {
            errno = EPROTO;
            return -1;
        }
        if (field == 4 || field == 22) {
            errno = 0;
            unsigned long long value = strtoull(token, &end, 10);
            if (errno || *end || *token == '-' ||
                (field == 4 && value > INT32_MAX)) {
                errno = EPROTO;
                return -1;
            }
            if (field == 4)
                identity->parent = (pid_t)value;
            else
                identity->start = value;
        }
        token = strtok_r(NULL, " \n", &save);
    }
    return 0;
}

#ifndef RAM_RUNNER_PROTOCOL_ONLY
static int process_gate(void) {
    struct process_identity identity;
    if (process_identity(getpid(), &identity))
        return -1;
    int fd = (int)syscall(SYS_pidfd_open, getpid(), 0U);
    if (fd < 0)
        return -1;
    int result = (int)syscall(SYS_pidfd_send_signal, fd, 0, NULL, 0U);
    int saved = errno;
    if (close(fd))
        return -1;
    errno = saved;
    return result;
}
#endif

static int signal_owned_children(int signal_number) {
    DIR* directory = opendir("/proc");
    if (!directory)
        return -1;
    int failure = 0;
    unsigned count = 0;
    struct dirent* entry;
    while (true) {
        errno = 0;
        entry = readdir(directory);
        if (!entry) {
            if (errno)
                failure = errno;
            break;
        }
        char* end;
        if (++count > 8192) {
            failure = EOVERFLOW;
            break;
        }
        errno = 0;
        long number = strtol(entry->d_name, &end, 10);
        if (errno || *end || number <= 0 || number > INT32_MAX)
            continue;
        pid_t pid = (pid_t)number;
        struct process_identity before, after;
        int result = process_identity(pid, &before);
        if (result == 1)
            continue;
        if (result < 0) {
            failure = errno;
            break;
        }
        if (before.parent != getpid())
            continue;
        int fd = (int)syscall(SYS_pidfd_open, pid, 0U);
        if (fd < 0) {
            if (errno == ESRCH)
                continue;
            failure = errno;
            break;
        }
        result = process_identity(pid, &after);
        if (result < 0)
            failure = errno;
        else if (!result &&
                 (after.parent != getpid() || before.start != after.start))
            failure = ESTALE;
        else if (!result) {
            record_signal_attempt(signal_number);
            if (syscall(SYS_pidfd_send_signal, fd, signal_number, NULL, 0U) &&
                errno != ESRCH)
                failure = errno;
        }
        if (close(fd) && !failure)
            failure = errno;
        if (failure)
            break;
        errno = 0;
    }
    if (closedir(directory) && !failure)
        failure = errno;
    if (failure) {
        errno = failure;
        return -1;
    }
    return 0;
}

static int terminate_children(void) {
    if (!child_group)
        return 0;
    int64_t deadline = now_ms() + CLEANUP_MS;
    int64_t kill_at = now_ms() + TERM_MS;
    bool killed = false;
    unsigned attempts = 0;
    while (true) {
        int status;
        pid_t pid = waitpid(-1, &status, WNOHANG);
        if (pid < 0 && errno == ECHILD) {
            child_group = 0;
            descendants_cleanup_completed = true;
            return 0;
        }
        if (pid < 0 && errno != EINTR) {
            cleanup_fail(errno);
            return -1;
        }
        if (pid > 0) {
            record_reap(pid, status);
        }
        /* Reap progress must still advance escalation and the total budget. */
        int64_t current = now_ms();
        if (current < 0)
            ++attempts;
        if (!killed && ((current >= 0 && current >= kill_at) ||
                        (current < 0 && attempts >= TERM_FALLBACK_ATTEMPTS))) {
            killed = true;
        }
        if ((current >= 0 && current >= deadline) ||
            (current < 0 && attempts >= CLEANUP_FALLBACK_ATTEMPTS)) {
            cleanup_fail(ETIMEDOUT);
            return -1;
        }
        if (signal_owned_children(killed ? SIGKILL : SIGTERM))
            cleanup_fail(errno);
        if (pid > 0)
            continue;
        struct timespec delay = {.tv_nsec = 20000000};
        nanosleep(&delay, NULL);
    }
}
