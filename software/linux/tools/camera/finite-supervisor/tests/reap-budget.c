/* Deterministic regression: waitpid always makes reap progress while the
 * monotonic clock advances. No real children are launched by this fixture. */
#define _GNU_SOURCE
#include <sys/wait.h>
#include <time.h>

static pid_t fixture_waitpid(pid_t pid, int* status, int flags);
static int fixture_clock_gettime(clockid_t clock, struct timespec* result);

#define main supervisor_unused_main
#define waitpid fixture_waitpid
#define clock_gettime fixture_clock_gettime
#include "../supervisor.c"
#undef main
#undef waitpid
#undef clock_gettime

static pid_t fixture_waitpid(pid_t pid, int* status, int flags) {
    (void)pid;
    (void)flags;
    *status = 0;
    return 123456;
}

static int fixture_clock_gettime(clockid_t clock, struct timespec* result) {
    (void)clock;
    static long milliseconds;
    milliseconds += 100;
    result->tv_sec = milliseconds / 1000;
    result->tv_nsec = milliseconds % 1000 * 1000000;
    return 0;
}

int main(void) {
    child_group = 1;
    const int result = terminate_children();
    printf("{\"result\":%d,\"cleanup_errno\":%d,\"adopted_reaped\":%u,"
           "\"descendants_cleanup_completed\":%s}\n",
           result,
           cleanup_error,
           adopted_reaped,
           descendants_cleanup_completed ? "true" : "false");
    return result == -1 && cleanup_error == ETIMEDOUT && adopted_reaped > 0 &&
                   !descendants_cleanup_completed
               ? 0
               : 1;
}
