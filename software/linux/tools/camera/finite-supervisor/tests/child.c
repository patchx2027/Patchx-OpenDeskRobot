#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef CHILD_MODE
#define CHILD_MODE 0
#endif

int main(int argc, char** argv) {
    if (argc != 8 || strcmp(argv[1], "--library-path") ||
        strcmp(argv[2], "/lib-fixture") || strcmp(argv[3], "/helper-fixture") ||
        strcmp(argv[4], "--capture-once") || strcmp(argv[5], "/iq-fixture") ||
        strcmp(argv[6], "/dev/video-fixture")) {
        return 31;
    }
    if (!getenv("LD_BIND_NOW") || strcmp(getenv("LD_BIND_NOW"), "1") ||
        !getenv("J2S_NO_CACHE") || strcmp(getenv("J2S_NO_CACHE"), "1") ||
        getenv("HDR_MODE") || getenv("USE_AS_FAKE_CAM")) {
        return 32;
    }
    struct rlimit limit;
    if (getrlimit(RLIMIT_FSIZE, &limit) || limit.rlim_cur != 4U * 1024U * 1024U) {
        return 33;
    }
    if (getrlimit(RLIMIT_CORE, &limit) || limit.rlim_cur != 0) {
        return 38;
    }
    for (int fd = 3; fd < 256; ++fd) {
        if (fcntl(fd, F_GETFD) >= 0) {
            return 34;
        }
    }
    printf("MAIN %ld\n", (long)getpid());
    fflush(stdout);
    if (CHILD_MODE == 1) {
        return 7;
    }
    if (CHILD_MODE == 2 || CHILD_MODE == 3) {
        if (CHILD_MODE == 3) {
            signal(SIGTERM, SIG_IGN);
        }
        for (;;) {
            pause();
        }
    }
    if (CHILD_MODE == 4) {
        pid_t pid = fork();
        if (pid < 0) {
            return 35;
        }
        if (!pid) {
            if (setsid() < 0) {
                _exit(36);
            }
            signal(SIGTERM, SIG_IGN);
            printf("ORPHAN %ld\n", (long)getpid());
            fflush(stdout);
            for (;;) {
                pause();
            }
        }
        usleep(50000);
        return 0;
    }
    if (CHILD_MODE == 5) {
        char bytes[4096];
        memset(bytes, 'x', sizeof(bytes));
        for (;;) {
            if (write(STDOUT_FILENO, bytes, sizeof(bytes)) < 0) {
                return 37;
            }
        }
    }
    if (CHILD_MODE == 6) {
        /* Four bounded workers produce orphaned exiting leaves. Maximum 512
         * leaves and finite workers keep the fixture safe even on a regression. */
        for (unsigned worker = 0; worker < 4; ++worker) {
            pid_t pid = fork();
            if (pid < 0)
                return 39;
            if (!pid) {
                if (setsid() < 0)
                    _exit(40);
                signal(SIGTERM, SIG_IGN);
                printf("CHURN %ld\n", (long)getpid());
                fflush(stdout);
                for (unsigned iteration = 0; iteration < 128; ++iteration) {
                    pid_t intermediate = fork();
                    if (intermediate < 0)
                        _exit(41);
                    if (!intermediate) {
                        pid_t leaf = fork();
                        if (leaf < 0)
                            _exit(42);
                        if (!leaf) {
                            printf("CHURN %ld\n", (long)getpid());
                            fflush(stdout);
                        }
                        _exit(0);
                    }
                    while (waitpid(intermediate, NULL, 0) < 0 && errno == EINTR) {
                    }
                    usleep(5000);
                }
                _exit(0);
            }
        }
        usleep(50000);
        return 0;
    }
    return 0;
}
