#define _GNU_SOURCE
#include "rtctrl/adapters/v4l2/capture.h"
#include <errno.h>
#include <fcntl.h>
#include <linux/media.h>
#include <linux/v4l2-subdev.h>
#include <linux/videodev2.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#ifndef CAPTURE_SOURCE_SHA
#define CAPTURE_SOURCE_SHA "unbound"
#endif
#define MAIN_BUDGET_NS INT64_C(8000000000)
#define CLEANUP_BUDGET_NS INT64_C(2000000000)
#define OUTPUT_BUDGET_NS INT64_C(2000000000)
#define MAX_JSON_BYTES (4U * 1024U * 1024U)
#define MAX_ATTEMPTS 32768U
#define MAX_OUTPUT_BYTES (16U * 1024U * 1024U)
#define WARMUP_FRAMES 30U
#define MAX_ENUM_FORMATS 64U
#define MAX_ENTITIES 256U
#define MAX_PADS 32U
#define MAX_LINKS 64U
#define MAX_SUBDEV_PADS 8U
#define MAX_SUBDEV_CODES 32U

struct entity_record {
    struct media_entity_desc entity;
    struct media_pad_desc pads[MAX_PADS];
    struct media_link_desc links[MAX_LINKS];
};
struct subdev_record {
    struct v4l2_subdev_format format;
    uint32_t codes[MAX_SUBDEV_CODES];
    unsigned code_count;
};

struct diagnostic {
    const char* device;
    int inspect_media;
    int inspect_subdev;
    struct media_device_info media_info;
    struct entity_record entities[MAX_ENTITIES];
    unsigned entity_count;
    int media_complete;
    struct subdev_record subdevs[MAX_SUBDEV_PADS];
    unsigned subdev_count;
    struct stat identity;
    struct v4l2_capability capability;
    struct v4l2_format native;
    struct v4l2_format initial_native;
    struct v4l2_format requested_native;
    struct v4l2_format sfmt_native;
    int initial_ready;
    int request_640_nv12;
    int sfmt_ready;
    int verifying_format;
    int format_verified;
    unsigned sfmt_attempts;
    unsigned gfmt_verify_attempts;
    unsigned reqbufs_attempts;
    struct v4l2_fmtdesc formats[MAX_ENUM_FORMATS];
    unsigned format_count;
    int identity_ready;
    int capability_ready;
    int camera_fd;
    int cleanup;
    int first_error;
    const char* first_phase;
    int64_t deadline_ns;
    int64_t started_ns;
    unsigned ioctl_attempts;
    unsigned cleanup_ioctl_attempts;
    unsigned streamon_attempts;
    unsigned streamoff_attempts;
    int streamoff_errno;
    unsigned munmap_attempts;
    int munmap_errno;
    unsigned device_close_attempts;
    int device_close_errno;
    int output_close_errno;
    int fd_flags_restore_errno;
    unsigned acquire_attempts;
    unsigned frames;
    unsigned corrupt;
    unsigned warmup;
    int saved;
    int capture_started;
    struct rtctrl_camera_frame frame;
};
static struct diagnostic diagnostic;
static volatile sig_atomic_t cancelled;
static char json_storage[MAX_JSON_BYTES];

int __wrap_close(int);
static int bounded_ioctl(int, unsigned long, void*);
static int validate_capture_native(void);
int __real_open(const char*, int, ...);
int __real_close(int);
int __real_ioctl(int, unsigned long, ...);
void* __real_mmap(void*, size_t, int, int, int, off_t);
int __real_munmap(void*, size_t);
int __real_poll(struct pollfd*, nfds_t, int);
ssize_t __real_write(int, const void*, size_t);
#ifdef CAPTURE_FIXTURE
int fixture_open(const char*, int, mode_t);
int fixture_close(int);
int fixture_ioctl(int, unsigned long, void*);
int fixture_fstat(int, struct stat*);
void* fixture_mmap(void*, size_t, int, int, int, off_t);
int fixture_munmap(void*, size_t);
int fixture_poll(struct pollfd*, nfds_t, int);
int64_t fixture_now_ns(void);
ssize_t fixture_write(int, const void*, size_t);
#define raw_open fixture_open
#define raw_close fixture_close
#define raw_ioctl fixture_ioctl
#define raw_fstat fixture_fstat
#define raw_mmap fixture_mmap
#define raw_munmap fixture_munmap
#define raw_poll fixture_poll
#define raw_write fixture_write
#else
#define raw_open __real_open
#define raw_close __real_close
#define raw_ioctl __real_ioctl
#define raw_fstat fstat
#define raw_mmap __real_mmap
#define raw_munmap __real_munmap
#define raw_poll __real_poll
#define raw_write __real_write
#endif

static int64_t now_ns(void) {
#ifdef CAPTURE_FIXTURE
    return fixture_now_ns();
#else
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) < 0) {
        return -1;
    }
    return (int64_t)now.tv_sec * INT64_C(1000000000) + now.tv_nsec;
#endif
}
static void fail_once(int error, const char* phase) {
    if (!diagnostic.first_error && error) {
        diagnostic.first_error = error;
        diagnostic.first_phase = phase;
    }
}
static int check_budget(void) {
    const int64_t now = now_ns();
    if (now < 0) {
        errno = EIO;
        return -1;
    }
    if (!diagnostic.cleanup && cancelled) {
        errno = ECANCELED;
        return -1;
    }
    if (now >= diagnostic.deadline_ns) {
        errno = ETIMEDOUT;
        return -1;
    }
    return 0;
}
static void signal_handler(int number) {
    if (number == SIGINT || number == SIGTERM) {
        cancelled = 1;
    }
}
static int start_timer(void) {
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = signal_handler;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGALRM, &action, NULL) < 0 ||
        sigaction(SIGINT, &action, NULL) < 0 ||
        sigaction(SIGTERM, &action, NULL) < 0) {
        return -1;
    }
    action.sa_handler = SIG_IGN;
    if (sigaction(SIGPIPE, &action, NULL) < 0) {
        return -1;
    }
    const struct itimerval timer = {{0, 100000}, {0, 100000}};
    return setitimer(ITIMER_REAL, &timer, NULL);
}
static void start_cleanup(void) {
    if (diagnostic.cleanup) {
        return;
    }
    diagnostic.cleanup = 1;
    const int64_t now = now_ns();
    diagnostic.deadline_ns = now < 0 ? 0 : now + CLEANUP_BUDGET_NS;
}
static void start_final_output(void) {
    /* The final diagnostic has an explicit independent 2s output budget.
     * Cancellation does not suppress reporting an already collected failure. */
    diagnostic.cleanup = 1;
    const int64_t now = now_ns();
    diagnostic.deadline_ns = now < 0 ? 0 : now + OUTPUT_BUDGET_NS;
}
static int same_identity(const struct stat* a, const struct stat* b) {
    return a->st_dev == b->st_dev && a->st_ino == b->st_ino &&
           a->st_rdev == b->st_rdev && S_ISCHR(b->st_mode);
}

int __wrap_open(const char* path, int flags, ...) {
    mode_t mode = 0;
    if (flags & O_CREAT) {
        va_list arguments;
        va_start(arguments, flags);
        mode = (mode_t)va_arg(arguments, int);
        va_end(arguments);
    }
    const int is_camera = diagnostic.device && !strcmp(path, diagnostic.device);
    if (is_camera && check_budget() < 0) {
        return -1;
    }
    const int fd = raw_open(path, flags, mode);
    if (fd < 0 || !is_camera) {
        return fd;
    }
    diagnostic.camera_fd = fd;
    struct stat identity;
    const int stat_result = raw_fstat(fd, &identity);
    const int identity_error =
        stat_result < 0              ? errno
        : !S_ISCHR(identity.st_mode) ? ENOTTY
        : diagnostic.identity_ready &&
                !same_identity(&diagnostic.identity, &identity)
            ? ENODEV
            : 0;
    if (identity_error) {
        __wrap_close(fd);
        errno = identity_error;
        return -1;
    }
    if (!diagnostic.identity_ready) {
        diagnostic.identity = identity;
        diagnostic.identity_ready = 1;
    }
    return fd;
}
int __wrap_close(int fd) {
    const int is_camera = fd == diagnostic.camera_fd;
    if (is_camera && diagnostic.capture_started && !diagnostic.cleanup) {
        start_cleanup();
    }
    if (is_camera) {
        ++diagnostic.device_close_attempts;
        diagnostic.camera_fd = -1;
    }
    /* Never retry close(EINTR): fd reuse would make a retry unsafe. */
    const int result = raw_close(fd);
    if (result < 0 && is_camera && !diagnostic.device_close_errno) {
        diagnostic.device_close_errno = errno;
    }
    return result;
}
static int same_gfmt(const struct v4l2_pix_format_mplane* current) {
    const struct v4l2_pix_format_mplane* before = &diagnostic.native.fmt.pix_mp;
    if (current->width != before->width || current->height != before->height ||
        current->pixelformat != before->pixelformat ||
        current->field != before->field ||
        current->colorspace != before->colorspace ||
        current->xfer_func != before->xfer_func ||
        current->quantization != before->quantization ||
        current->ycbcr_enc != before->ycbcr_enc ||
        current->num_planes != before->num_planes ||
        current->num_planes > RTCTRL_CAMERA_MAX_PLANES) {
        return 0;
    }
    for (unsigned p = 0; p < current->num_planes; ++p) {
        if (current->plane_fmt[p].bytesperline !=
                before->plane_fmt[p].bytesperline ||
            current->plane_fmt[p].sizeimage != before->plane_fmt[p].sizeimage) {
            return 0;
        }
    }
    return 1;
}
int __wrap_ioctl(int fd, unsigned long request, ...) {
    va_list arguments;
    va_start(arguments, request);
    void* argument = va_arg(arguments, void*);
    va_end(arguments);
    const int is_camera = fd == diagnostic.camera_fd;
    const int setting_format = is_camera && request == VIDIOC_S_FMT &&
                               diagnostic.request_640_nv12 &&
                               diagnostic.capture_started;
    if (setting_format) {
        struct v4l2_format* format = argument;
        ++diagnostic.sfmt_attempts;
        if (format->type != V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE ||
            format->fmt.pix_mp.width != 640 || format->fmt.pix_mp.height != 480 ||
            format->fmt.pix_mp.pixelformat != V4L2_PIX_FMT_NV12) {
            errno = EINVAL;
            fail_once(errno, "set-format-request");
            return -1;
        }
        format->fmt.pix_mp.field = V4L2_FIELD_NONE;
        format->fmt.pix_mp.num_planes = 1;
        diagnostic.requested_native = *format;
    }
    if (is_camera && request == VIDIOC_G_FMT && diagnostic.verifying_format) {
        ++diagnostic.gfmt_verify_attempts;
    }
    if (is_camera && request == VIDIOC_REQBUFS &&
        ((struct v4l2_requestbuffers*)argument)->count) {
        ++diagnostic.reqbufs_attempts;
        if (diagnostic.request_640_nv12 && !diagnostic.format_verified) {
            errno = EPROTO;
            fail_once(errno, "request-buffers-before-format");
            return -1;
        }
    }
    if (is_camera && request == VIDIOC_STREAMON) {
        ++diagnostic.streamon_attempts;
        if (diagnostic.request_640_nv12 && !diagnostic.format_verified) {
            errno = EPROTO;
            fail_once(errno, "stream-before-format");
            return -1;
        }
    }
    if (is_camera && request == VIDIOC_STREAMOFF) {
        if (!diagnostic.cleanup) {
            start_cleanup();
        }
        ++diagnostic.streamoff_attempts;
    }
    int result;
    unsigned* attempts = diagnostic.cleanup ? &diagnostic.cleanup_ioctl_attempts
                                            : &diagnostic.ioctl_attempts;
    if (is_camera && ++*attempts > MAX_ATTEMPTS) {
        errno = ELOOP;
        result = -1;
    } else {
        result =
            is_camera && check_budget() < 0 ? -1 : raw_ioctl(fd, request, argument);
    }
    /* Existing library retries EINTR; translate only when its private deadline
     * or user cancellation is reached. Periodic signals interrupt restartable
     * calls, but cannot stop a driver sleeping in uninterruptible kernel code. */
    if (result < 0 && errno == EINTR && is_camera && check_budget() < 0) {
        result = -1;
    }
    if (!result && is_camera && request == VIDIOC_QUERYCAP &&
        diagnostic.capability_ready) {
        const struct v4l2_capability* capability = argument;
        if (memcmp(capability->driver,
                   diagnostic.capability.driver,
                   sizeof(capability->driver)) ||
            memcmp(capability->card,
                   diagnostic.capability.card,
                   sizeof(capability->card)) ||
            memcmp(capability->bus_info,
                   diagnostic.capability.bus_info,
                   sizeof(capability->bus_info)) ||
            capability->capabilities != diagnostic.capability.capabilities ||
            capability->device_caps != diagnostic.capability.device_caps) {
            errno = ENODEV;
            result = -1;
        }
    }
    if (!result && is_camera && request == VIDIOC_G_FMT &&
        diagnostic.capture_started &&
        (((struct v4l2_format*)argument)->type !=
             V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE ||
         !same_gfmt(&((struct v4l2_format*)argument)->fmt.pix_mp))) {
        errno = EPROTO;
        result = -1;
    }
    if (!result && is_camera && request == VIDIOC_G_FMT &&
        diagnostic.capture_started && !diagnostic.request_640_nv12) {
        diagnostic.format_verified = 1;
    }
    if (setting_format && !result) {
        const struct v4l2_format* returned = argument;
        diagnostic.sfmt_native = *returned;
        diagnostic.sfmt_ready = 1;
        diagnostic.native = *returned;
        const struct v4l2_pix_format_mplane* format = &returned->fmt.pix_mp;
        int error = returned->type != V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE ||
                            format->width != 640 || format->height != 480 ||
                            format->pixelformat != V4L2_PIX_FMT_NV12 ||
                            format->field != V4L2_FIELD_NONE ||
                            format->num_planes != 1
                        ? EPROTO
                        : validate_capture_native();
        if (error) {
            fail_once(error, "set-format-return");
            errno = error;
            result = -1;
        } else {
            struct v4l2_format verified;
            memset(&verified, 0, sizeof(verified));
            verified.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
            diagnostic.verifying_format = 1;
            result = bounded_ioctl(fd, VIDIOC_G_FMT, &verified);
            diagnostic.verifying_format = 0;
            if (result < 0) {
                fail_once(errno, "get-format-verify");
            } else {
                diagnostic.native = verified;
                diagnostic.format_verified = 1;
            }
        }
    } else if (setting_format && result < 0 && errno != EINTR) {
        fail_once(errno, "set-format");
    }
    if (result < 0 && is_camera && request == VIDIOC_STREAMOFF && errno != EINTR &&
        !diagnostic.streamoff_errno) {
        diagnostic.streamoff_errno = errno;
    }
    return result;
}
void* __wrap_mmap(
    void* address, size_t length, int protection, int flags, int fd, off_t offset) {
    if (fd == diagnostic.camera_fd &&
        (length > MAX_OUTPUT_BYTES || check_budget() < 0)) {
        if (length > MAX_OUTPUT_BYTES) {
            errno = EFBIG;
        }
        return MAP_FAILED;
    }
    return raw_mmap(address, length, protection, flags, fd, offset);
}
int __wrap_munmap(void* address, size_t length) {
    if (diagnostic.capture_started && !diagnostic.cleanup) {
        start_cleanup();
    }
    ++diagnostic.munmap_attempts;
    const int result = raw_munmap(address, length);
    if (result < 0 && !diagnostic.munmap_errno) {
        diagnostic.munmap_errno = errno;
    }
    return result;
}
int __wrap_poll(struct pollfd* descriptors, nfds_t count, int timeout) {
    if (check_budget() < 0) {
        return -1;
    }
    return raw_poll(descriptors, count, timeout);
}
/* glibc fortify may route the same library poll through __poll_chk, especially
 * under ASan. Keep its size check and the identical private budget. */
int __wrap___poll_chk(struct pollfd* descriptors,
                      nfds_t count,
                      int timeout,
                      size_t bytes) {
    if (count > bytes / sizeof(*descriptors)) {
        errno = EINVAL;
        return -1;
    }
    return __wrap_poll(descriptors, count, timeout);
}
ssize_t __wrap_write(int fd, const void* data, size_t size) {
    if (check_budget() < 0) {
        return -1;
    }
    return raw_write(fd, data, size);
}
static int bounded_ioctl(int fd, unsigned long request, void* argument) {
    for (unsigned attempt = 0; attempt < MAX_ATTEMPTS; ++attempt) {
        const int result = __wrap_ioctl(fd, request, argument);
        if (result == 0 || errno != EINTR) {
            return result;
        }
    }
    errno = ELOOP;
    return -1;
}

static void json_string(FILE* output, const char* value, size_t capacity) {
    fputc('"', output);
    for (size_t i = 0; i < capacity && value[i]; ++i) {
        const unsigned char byte = (unsigned char)value[i];
        if (byte == '"' || byte == '\\') {
            fputc('\\', output);
            fputc(byte, output);
        } else if (byte < 32 || byte >= 127) {
            fprintf(output, "\\u%04x", (unsigned)byte);
        } else {
            fputc(byte, output);
        }
    }
    fputc('"', output);
}
static int cleanup_uncertain(void) {
    return diagnostic.streamoff_errno || diagnostic.munmap_errno ||
           diagnostic.device_close_errno || diagnostic.output_close_errno ||
           diagnostic.fd_flags_restore_errno;
}
static void write_format_json(FILE* output, const struct v4l2_format* native) {
    if (!native) {
        fputs("null", output);
        return;
    }
    const struct v4l2_pix_format_mplane* format = &native->fmt.pix_mp;
    fprintf(output,
            "{\"type\":%u,\"width\":%u,\"height\":%u,\"native_format\":%u,"
            "\"field\":%u,\"plane_count\":%u,\"colorspace\":%u,\"quantization\":%u,"
            "\"transfer_function\":%u,\"ycbcr_encoding\":%u,\"planes\":[",
            native->type,
            format->width,
            format->height,
            format->pixelformat,
            format->field,
            format->num_planes,
            format->colorspace,
            format->quantization,
            format->xfer_func,
            format->ycbcr_enc);
    for (unsigned p = 0; p < format->num_planes && p < RTCTRL_CAMERA_MAX_PLANES;
         ++p) {
        fprintf(output,
                "%s{\"stride\":%u,\"sizeimage\":%u}",
                p ? "," : "",
                format->plane_fmt[p].bytesperline,
                format->plane_fmt[p].sizeimage);
    }
    fputs("]}", output);
}
static const char* capture_mode(void) {
    return diagnostic.request_640_nv12 ? "capture-once-640x480-nv12"
                                       : "capture-once";
}
static void write_json(FILE* output, const char* mode) {
    fprintf(output,
            "{\"schema_version\":1,\"diagnostic\":\"camera-bounded-format-v3\","
            "\"mode\":");
    json_string(output, mode, strlen(mode));
    fprintf(output,
            ",\"source_manifest_sha256\":\"%s\",\"device\":",
            CAPTURE_SOURCE_SHA);
    json_string(output, diagnostic.device, strlen(diagnostic.device));
    fprintf(output,
            ",\"rdev_major\":%u,\"rdev_minor\":%u,\"device_filesystem_device\":%llu,"
            "\"device_inode\":%llu,"
            "\"driver\":",
            major(diagnostic.identity.st_rdev),
            minor(diagnostic.identity.st_rdev),
            (unsigned long long)diagnostic.identity.st_dev,
            (unsigned long long)diagnostic.identity.st_ino);
    json_string(output,
                (const char*)diagnostic.capability.driver,
                sizeof(diagnostic.capability.driver));
    fprintf(output, ",\"initial_format\":");
    write_format_json(output,
                      diagnostic.initial_ready ? &diagnostic.initial_native : NULL);
    fprintf(output, ",\"requested_format\":");
    write_format_json(
        output, diagnostic.request_640_nv12 ? &diagnostic.requested_native : NULL);
    fprintf(output, ",\"set_format_returned\":");
    write_format_json(output,
                      diagnostic.sfmt_ready ? &diagnostic.sfmt_native : NULL);
    fprintf(output, ",\"actual_format\":");
    write_format_json(output,
                      diagnostic.format_verified ? &diagnostic.native : NULL);
    fprintf(
        output,
        ",\"format_verified\":%s,\"sfmt_attempts\":%u,\"gfmt_verify_attempts\":%u,"
        "\"reqbufs_attempts\":%u",
        diagnostic.format_verified ? "true" : "false",
        diagnostic.sfmt_attempts,
        diagnostic.gfmt_verify_attempts,
        diagnostic.reqbufs_attempts);
    fprintf(output, ",\"card\":");
    json_string(output,
                (const char*)diagnostic.capability.card,
                sizeof(diagnostic.capability.card));
    fprintf(output, ",\"bus_info\":");
    json_string(output,
                (const char*)diagnostic.capability.bus_info,
                sizeof(diagnostic.capability.bus_info));
    if (diagnostic.inspect_media) {
        fprintf(output, ",\"media_driver\":");
        json_string(output,
                    diagnostic.media_info.driver,
                    sizeof(diagnostic.media_info.driver));
        fprintf(output, ",\"media_model\":");
        json_string(output,
                    diagnostic.media_info.model,
                    sizeof(diagnostic.media_info.model));
        fprintf(output, ",\"media_bus_info\":");
        json_string(output,
                    diagnostic.media_info.bus_info,
                    sizeof(diagnostic.media_info.bus_info));
        fprintf(
            output,
            ",\"media_version\":%u,\"media_enumeration_complete\":%s,\"entities\":[",
            diagnostic.media_info.media_version,
            diagnostic.media_complete ? "true" : "false");
        for (unsigned e = 0; e < diagnostic.entity_count; ++e) {
            const struct entity_record* record = &diagnostic.entities[e];
            fprintf(
                output, "%s{\"id\":%u,\"name\":", e ? "," : "", record->entity.id);
            json_string(output, record->entity.name, sizeof(record->entity.name));
            fprintf(output,
                    ",\"type\":%u,\"flags\":%u,\"major\":%u,\"minor\":%u,\"pads\":[",
                    record->entity.type,
                    record->entity.flags,
                    record->entity.dev.major,
                    record->entity.dev.minor);
            for (unsigned pad = 0; pad < record->entity.pads; ++pad) {
                fprintf(output,
                        "%s{\"entity\":%u,\"index\":%u,\"flags\":%u}",
                        pad ? "," : "",
                        record->pads[pad].entity,
                        record->pads[pad].index,
                        record->pads[pad].flags);
            }
            fprintf(output, "],\"links\":[");
            for (unsigned link = 0; link < record->entity.links; ++link) {
                const struct media_link_desc* value = &record->links[link];
                fprintf(output,
                        "%s{\"source_entity\":%u,\"source_pad\":%u,"
                        "\"sink_entity\":%u,\"sink_pad\":%u,\"flags\":%u}",
                        link ? "," : "",
                        value->source.entity,
                        value->source.index,
                        value->sink.entity,
                        value->sink.index,
                        value->flags);
            }
            fprintf(output, "]}");
        }
        fprintf(output, "]");
    }
    if (diagnostic.inspect_subdev) {
        fprintf(output,
                ",\"subdev_pad_probe_limit\":8,\"subdev_pad_enumeration_complete\":"
                "false,\"subdev_active_formats\":[");
        for (unsigned p = 0; p < diagnostic.subdev_count; ++p) {
            const struct subdev_record* record = &diagnostic.subdevs[p];
            fprintf(output,
                    "%s{\"pad\":%u,\"width\":%u,\"height\":%u,"
                    "\"mbus_code\":%u,\"field\":%u,\"colorspace\":%u,"
                    "\"ycbcr_encoding\":%u,\"quantization\":%u,"
                    "\"transfer_function\":%u,\"codes\":[",
                    p ? "," : "",
                    record->format.pad,
                    record->format.format.width,
                    record->format.format.height,
                    record->format.format.code,
                    record->format.format.field,
                    record->format.format.colorspace,
                    record->format.format.ycbcr_enc,
                    record->format.format.quantization,
                    record->format.format.xfer_func);
            for (unsigned c = 0; c < record->code_count; ++c) {
                fprintf(output, "%s%u", c ? "," : "", record->codes[c]);
            }
            fprintf(output, "]}");
        }
        fprintf(output, "]");
    }
    const struct v4l2_pix_format_mplane* format = &diagnostic.native.fmt.pix_mp;
    fprintf(output,
            ",\"capabilities\":%u,\"device_caps\":%u,\"width\":%u,"
            "\"height\":%u,\"native_format\":%u,\"field\":%u,"
            "\"colorspace\":%u,\"quantization\":%u,"
            "\"transfer_function\":%u,\"ycbcr_encoding\":%u,\"formats\":[",
            diagnostic.capability.capabilities,
            diagnostic.capability.device_caps,
            format->width,
            format->height,
            format->pixelformat,
            format->field,
            format->colorspace,
            format->quantization,
            format->xfer_func,
            format->ycbcr_enc);
    for (unsigned i = 0; i < diagnostic.format_count; ++i) {
        fprintf(output,
                "%s{\"fourcc\":%u,\"flags\":%u,\"description\":",
                i ? "," : "",
                diagnostic.formats[i].pixelformat,
                diagnostic.formats[i].flags);
        json_string(output,
                    (const char*)diagnostic.formats[i].description,
                    sizeof(diagnostic.formats[i].description));
        fputc('}', output);
    }
    fprintf(output, "],\"planes\":[");
    for (unsigned p = 0; p < format->num_planes && p < RTCTRL_CAMERA_MAX_PLANES;
         ++p) {
        const struct rtctrl_frame_plane* plane = &diagnostic.frame.planes[p];
        fprintf(output,
                "%s{\"stride\":%u,\"sizeimage\":%u,\"payload\":%zu,"
                "\"allocation\":%zu,\"offset\":%zu}",
                p ? "," : "",
                format->plane_fmt[p].bytesperline,
                format->plane_fmt[p].sizeimage,
                plane->size,
                plane->allocation_size,
                plane->data_offset);
    }
    fprintf(output,
            "],\"sequence\":%u,\"timestamp_ns\":%lld,"
            "\"timestamp_monotonic\":%d,\"first_errno\":%d,\"first_phase\":",
            diagnostic.frame.sequence,
            (long long)diagnostic.frame.timestamp_ns,
            diagnostic.frame.timestamp_monotonic,
            diagnostic.first_error);
    const char* phase = diagnostic.first_phase ? diagnostic.first_phase : "none";
    json_string(output, phase, strlen(phase));
    fprintf(output,
            ",\"acquire_attempts\":%u,\"frames\":%u,\"warmup_frames\":%u,"
            "\"corrupt_frames\":%u,\"saved\":%s,\"main_budget_ms\":8000,"
            "\"cleanup_budget_ms\":2000,\"final_output_budget_ms\":2000,"
            "\"json_capacity_bytes\":4194304,\"elapsed_ms\":%lld,"
            "\"ioctl_attempts\":%u,\"cleanup_ioctl_attempts\":%u,"
            "\"streamon_attempts\":%u,\"streamoff_attempts\":%u,"
            "\"streamoff_errno\":%d,\"munmap_attempts\":%u,"
            "\"munmap_errno\":%d,\"device_close_attempts\":%u,"
            "\"device_close_errno\":%d,\"output_close_errno\":%d,"
            "\"fd_flags_restore_errno\":%d,\"cleanup_uncertain\":%s,"
            "\"physical_image_quality_verified\":false}\n",
            diagnostic.acquire_attempts,
            diagnostic.frames,
            diagnostic.warmup,
            diagnostic.corrupt,
            diagnostic.saved ? "true" : "false",
            (long long)((now_ns() - diagnostic.started_ns) / 1000000),
            diagnostic.ioctl_attempts,
            diagnostic.cleanup_ioctl_attempts,
            diagnostic.streamon_attempts,
            diagnostic.streamoff_attempts,
            diagnostic.streamoff_errno,
            diagnostic.munmap_attempts,
            diagnostic.munmap_errno,
            diagnostic.device_close_attempts,
            diagnostic.device_close_errno,
            diagnostic.output_close_errno,
            diagnostic.fd_flags_restore_errno,
            cleanup_uncertain() ? "true" : "false");
}

static int inspect_device(void) {
    const int fd = __wrap_open(diagnostic.device, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) {
        fail_once(errno, "inspect-open");
        return -1;
    }
    int result = bounded_ioctl(fd, VIDIOC_QUERYCAP, &diagnostic.capability);
    if (result < 0) {
        fail_once(errno, "querycap");
    } else {
        diagnostic.capability_ready = 1;
        const uint32_t flags =
            (diagnostic.capability.capabilities & V4L2_CAP_DEVICE_CAPS)
                ? diagnostic.capability.device_caps
                : diagnostic.capability.capabilities;
        if (!(flags & V4L2_CAP_VIDEO_CAPTURE_MPLANE) ||
            !(flags & V4L2_CAP_STREAMING)) {
            fail_once(ENOTSUP, "capture-mplane-capability");
            result = -1;
        }
    }
    if (!result) {
        diagnostic.native.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
        result = bounded_ioctl(fd, VIDIOC_G_FMT, &diagnostic.native);
        if (result < 0) {
            fail_once(errno, "get-format");
        }
    }
    if (!result) {
        const struct v4l2_pix_format_mplane* format = &diagnostic.native.fmt.pix_mp;
        if (!format->width || !format->height || !format->num_planes ||
            format->num_planes > RTCTRL_CAMERA_MAX_PLANES) {
            fail_once(EPROTO, "get-format-metadata");
            result = -1;
        }
    }
    if (!result) {
        diagnostic.initial_native = diagnostic.native;
        diagnostic.initial_ready = 1;
        unsigned index;
        for (index = 0; index <= MAX_ENUM_FORMATS; ++index) {
            struct v4l2_fmtdesc format = {0};
            format.index = index;
            format.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
            if (bounded_ioctl(fd, VIDIOC_ENUM_FMT, &format) < 0) {
                if (errno != EINVAL) {
                    fail_once(errno, "enum-format");
                    result = -1;
                }
                break;
            }
            if (index == MAX_ENUM_FORMATS) {
                fail_once(EOVERFLOW, "enum-format-limit");
                result = -1;
                break;
            }
            diagnostic.formats[diagnostic.format_count++] = format;
        }
    }
    if (__wrap_close(fd) < 0) {
        fail_once(errno, "inspect-close");
        result = -1;
    }
    return result;
}
static int validate_entity_record(const struct entity_record* record) {
    uint32_t seen_pads = 0;
    for (unsigned p = 0; p < record->entity.pads; ++p) {
        const struct media_pad_desc* pad = &record->pads[p];
        if (pad->entity != record->entity.id || pad->index >= record->entity.pads ||
            (seen_pads & (UINT32_C(1) << pad->index))) {
            return EPROTO;
        }
        seen_pads |= UINT32_C(1) << pad->index;
    }
    for (unsigned l = 0; l < record->entity.links; ++l) {
        const struct media_link_desc* link = &record->links[l];
        if (!link->source.entity || !link->sink.entity ||
            link->source.index >= MAX_PADS || link->sink.index >= MAX_PADS ||
            (link->source.entity != record->entity.id &&
             link->sink.entity != record->entity.id) ||
            (link->source.entity == record->entity.id &&
             link->source.index >= record->entity.pads) ||
            (link->sink.entity == record->entity.id &&
             link->sink.index >= record->entity.pads)) {
            return EPROTO;
        }
    }
    return 0;
}
static int inspect_media_device(void) {
    const int fd = __wrap_open(diagnostic.device, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) {
        fail_once(errno, "media-open");
        return -1;
    }
    int result = bounded_ioctl(fd, MEDIA_IOC_DEVICE_INFO, &diagnostic.media_info);
    if (result < 0) {
        fail_once(errno, "media-device-info");
    }
    uint32_t previous = 0;
    while (!result) {
        struct media_entity_desc entity = {0};
        entity.id = previous | MEDIA_ENT_ID_FLAG_NEXT;
        if (bounded_ioctl(fd, MEDIA_IOC_ENUM_ENTITIES, &entity) < 0) {
            if (errno != EINVAL) {
                fail_once(errno, "media-enum-entities");
                result = -1;
            } else {
                diagnostic.media_complete = 1;
            }
            break;
        }
        if (diagnostic.entity_count >= MAX_ENTITIES || !entity.id ||
            entity.id <= previous || (entity.id & MEDIA_ENT_ID_FLAG_NEXT) ||
            entity.pads > MAX_PADS || entity.links > MAX_LINKS) {
            fail_once(EOVERFLOW, "media-entity-bounds");
            result = -1;
            break;
        }
        struct entity_record* record = &diagnostic.entities[diagnostic.entity_count];
        record->entity = entity;
        struct media_links_enum enumeration = {0};
        enumeration.entity = entity.id;
        enumeration.pads = record->pads;
        enumeration.links = record->links;
        if (bounded_ioctl(fd, MEDIA_IOC_ENUM_LINKS, &enumeration) < 0) {
            fail_once(errno, "media-enum-links");
            result = -1;
            break;
        }
        const int error = validate_entity_record(record);
        if (error) {
            fail_once(error, "media-link-metadata");
            result = -1;
            break;
        }
        ++diagnostic.entity_count;
        previous = entity.id;
    }
    if (__wrap_close(fd) < 0) {
        fail_once(errno, "media-close");
        result = -1;
    }
    return result;
}
static int inspect_subdev_device(void) {
    const int fd = __wrap_open(diagnostic.device, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) {
        fail_once(errno, "subdev-open");
        return -1;
    }
    int result = 0;
    for (unsigned p = 0; p < MAX_SUBDEV_PADS; ++p) {
        struct v4l2_subdev_format format = {0};
        format.which = V4L2_SUBDEV_FORMAT_ACTIVE;
        format.pad = p;
        if (bounded_ioctl(fd, VIDIOC_SUBDEV_G_FMT, &format) < 0) {
            if (errno == EINVAL) {
                continue;
            }
            fail_once(errno, "subdev-get-format");
            result = -1;
            break;
        }
        if (format.pad != p || !format.format.width || !format.format.height ||
            !format.format.code) {
            fail_once(EPROTO, "subdev-format-metadata");
            result = -1;
            break;
        }
        struct subdev_record* record =
            &diagnostic.subdevs[diagnostic.subdev_count++];
        record->format = format;
        for (unsigned i = 0; i <= MAX_SUBDEV_CODES; ++i) {
            struct v4l2_subdev_mbus_code_enum code = {0};
            code.which = V4L2_SUBDEV_FORMAT_ACTIVE;
            code.pad = p;
            code.index = i;
            if (bounded_ioctl(fd, VIDIOC_SUBDEV_ENUM_MBUS_CODE, &code) < 0) {
                if (errno != EINVAL) {
                    fail_once(errno, "subdev-enum-code");
                    result = -1;
                }
                break;
            }
            if (i == MAX_SUBDEV_CODES || !code.code) {
                fail_once(EOVERFLOW, "subdev-code-limit");
                result = -1;
                break;
            }
            record->codes[record->code_count++] = code.code;
        }
        if (result) {
            break;
        }
    }
    if (!result && !diagnostic.subdev_count) {
        fail_once(ENODATA, "subdev-no-active-format");
        result = -1;
    }
    if (__wrap_close(fd) < 0) {
        fail_once(errno, "subdev-close");
        result = -1;
    }
    return result;
}
static int same_native_format(const struct rtctrl_camera_format* format) {
    const struct v4l2_pix_format_mplane* native = &diagnostic.native.fmt.pix_mp;
    return format->width == native->width && format->height == native->height &&
           format->native_format == native->pixelformat &&
           format->plane_count == native->num_planes;
}
static int frame_payload_valid(const struct rtctrl_camera_frame* frame) {
    if (!same_native_format(&frame->format)) {
        return EPROTO;
    }
    const struct v4l2_pix_format_mplane* native = &diagnostic.native.fmt.pix_mp;
    if (!native->width || !native->height || native->width > 8192 ||
        native->height > 8192) {
        return EOVERFLOW;
    }
    size_t total = 0;
    for (unsigned p = 0; p < native->num_planes; ++p) {
        const struct rtctrl_frame_plane* plane = &frame->planes[p];
        if (!plane->data || !plane->size || !plane->allocation_size ||
            plane->data_offset > plane->allocation_size ||
            plane->size > plane->allocation_size - plane->data_offset ||
            !plane->stride || plane->stride != native->plane_fmt[p].bytesperline ||
            native->plane_fmt[p].sizeimage > plane->allocation_size) {
            return ENODATA;
        }
        uint64_t required = 0;
        switch (native->pixelformat) {
            case V4L2_PIX_FMT_NV12:
            case V4L2_PIX_FMT_NV21:
                if (native->num_planes != 1 || (native->width & 1U) ||
                    (native->height & 1U) || plane->stride < native->width) {
                    return EPROTO;
                }
                required = (uint64_t)plane->stride * native->height * 3U / 2U;
                break;
            case V4L2_PIX_FMT_NV12M:
            case V4L2_PIX_FMT_NV21M:
                if (native->num_planes != 2 || (native->width & 1U) ||
                    (native->height & 1U) || plane->stride < native->width) {
                    return EPROTO;
                }
                required = (uint64_t)plane->stride * native->height / (p ? 2U : 1U);
                break;
            case V4L2_PIX_FMT_GREY:
            case V4L2_PIX_FMT_RGB24:
            case V4L2_PIX_FMT_BGR24:
            case V4L2_PIX_FMT_YUYV:
            case V4L2_PIX_FMT_UYVY: {
                const unsigned bpp =
                    native->pixelformat == V4L2_PIX_FMT_GREY ? 1U
                    : native->pixelformat == V4L2_PIX_FMT_RGB24 ||
                            native->pixelformat == V4L2_PIX_FMT_BGR24
                        ? 3U
                        : 2U;
                if (native->num_planes != 1 ||
                    (uint64_t)plane->stride < (uint64_t)native->width * bpp) {
                    return EPROTO;
                }
                required = (uint64_t)plane->stride * native->height;
                break;
            }
            default:
                return ENOTSUP;
        }
        if (plane->size < required || plane->size > MAX_OUTPUT_BYTES - total) {
            return ENODATA;
        }
        total += plane->size;
    }
    return total ? 0 : ENODATA;
}
static int validate_capture_native(void) {
    const struct v4l2_pix_format_mplane* native = &diagnostic.native.fmt.pix_mp;
    if (native->width > 8192 || native->height > 8192) {
        return EOVERFLOW;
    }
    size_t allocation = 0;
    for (unsigned p = 0; p < native->num_planes; ++p) {
        const size_t size = native->plane_fmt[p].sizeimage;
        if (!size || size > MAX_OUTPUT_BYTES - allocation) {
            return EFBIG;
        }
        allocation += size;
    }
    struct rtctrl_camera_frame probe;
    memset(&probe, 0, sizeof(probe));
    probe.format.width = native->width;
    probe.format.height = native->height;
    probe.format.native_format = native->pixelformat;
    probe.format.plane_count = native->num_planes;
    for (unsigned p = 0; p < native->num_planes; ++p) {
        probe.planes[p].data = &probe;
        probe.planes[p].size = native->plane_fmt[p].sizeimage;
        probe.planes[p].allocation_size = native->plane_fmt[p].sizeimage;
        probe.planes[p].stride = native->plane_fmt[p].bytesperline;
    }
    return frame_payload_valid(&probe);
}
static int create_output(const char* path) {
    const int fd = __wrap_open(
        path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) {
        return -1;
    }
    struct stat identity;
    if (fstat(fd, &identity) < 0 || !S_ISREG(identity.st_mode)) {
        const int error = errno ? errno : EINVAL;
        __wrap_close(fd);
        errno = error;
        return -1;
    }
    return fd;
}
static int write_all(int fd, const void* data, size_t size) {
    const int original_flags = fcntl(fd, F_GETFL);
    if (original_flags < 0) {
        return -1;
    }
    const int changed = !(original_flags & O_NONBLOCK);
    if (changed && fcntl(fd, F_SETFL, original_flags | O_NONBLOCK) < 0) {
        return -1;
    }
    const unsigned char* bytes = data;
    unsigned attempts = 0;
    int error = 0;
    while (size) {
        if (++attempts > MAX_ATTEMPTS || check_budget() < 0) {
            error = attempts > MAX_ATTEMPTS ? ELOOP : errno;
            break;
        }
        const ssize_t count = write(fd, bytes, size);
        if (count < 0 && errno == EINTR) {
            continue;
        }
        if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            struct pollfd descriptor = {fd, POLLOUT, 0};
            const int ready = __wrap_poll(&descriptor, 1, 100);
            if (ready < 0 && errno != EINTR) {
                error = errno;
                break;
            }
            if (ready > 0 && (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL))) {
                error = EPIPE;
                break;
            }
            continue;
        }
        if (count <= 0) {
            error = count < 0 ? errno : EIO;
            break;
        }
        bytes += count;
        size -= (size_t)count;
    }
    /* O_NONBLOCK belongs to the shared open-file description. Restore even if
     * the budget expired; F_SETFL is a single non-waiting attempt, never retry. */
    if (changed && fcntl(fd, F_SETFL, original_flags) < 0) {
        diagnostic.fd_flags_restore_errno = errno;
        if (!error) {
            error = errno;
        }
    }
    if (error) {
        errno = error;
        return -1;
    }
    return 0;
}
static int emit_json_fd(int fd, const char* mode) {
    if (check_budget() < 0) {
        return -1;
    }
    FILE* memory = fmemopen(json_storage, sizeof(json_storage), "w");
    if (!memory) {
        return -1;
    }
    write_json(memory, mode);
    int error = ferror(memory) ? EIO : 0;
    if (fflush(memory) != 0 && !error) {
        error = errno ? errno : EIO;
    }
    const long length = ftell(memory);
    if (length < 0 || (size_t)length >= sizeof(json_storage)) {
        if (!error) {
            error = EOVERFLOW;
        }
    }
    if (fclose(memory) != 0 && !error) {
        error = errno ? errno : EIO;
    }
    if (error) {
        errno = error;
        return -1;
    }
    return write_all(fd, json_storage, (size_t)length);
}
static void capture_once(const char* output_path) {
    const int format_error =
        diagnostic.request_640_nv12 ? 0 : validate_capture_native();
    if (format_error) {
        fail_once(format_error, "capture-format-preflight");
        return;
    }
    char metadata_path[4096];
    const int length =
        snprintf(metadata_path, sizeof(metadata_path), "%s.json", output_path);
    if (length < 0 || (size_t)length >= sizeof(metadata_path)) {
        fail_once(ENAMETOOLONG, "output-path");
        return;
    }
    int output_fd = create_output(output_path);
    if (output_fd < 0) {
        fail_once(errno, "output-create");
        return;
    }
    int metadata_fd = create_output(metadata_path);
    if (metadata_fd < 0) {
        fail_once(errno, "metadata-create");
        if (__wrap_close(output_fd) < 0) {
            diagnostic.output_close_errno = errno;
        }
        return;
    }
    const struct rtctrl_v4l2_config config = {
        diagnostic.device,
        diagnostic.request_640_nv12 ? 640 : 0,
        diagnostic.request_640_nv12 ? 480 : 0,
        diagnostic.request_640_nv12 ? V4L2_PIX_FMT_NV12 : 0,
        4,
        0};
    struct rtctrl_camera* camera = NULL;
    diagnostic.capture_started = 1;
    int result = rtctrl_v4l2_open(&config, &camera);
    if (result) {
        fail_once(-result, "capture-open");
    }
    while (!diagnostic.first_error && !diagnostic.saved) {
        if (++diagnostic.acquire_attempts > MAX_ATTEMPTS || check_budget() < 0) {
            fail_once(diagnostic.acquire_attempts > MAX_ATTEMPTS ? ELOOP : errno,
                      "capture-budget");
            break;
        }
        struct rtctrl_camera_frame frame;
        result = rtctrl_camera_acquire(camera, 100, &frame);
        if (result == -EAGAIN || result == -EINTR || result == -ETIMEDOUT) {
            continue;
        }
        if (result) {
            fail_once(-result, "acquire");
            break;
        }
        ++diagnostic.frames;
        if (frame.corrupt) {
            ++diagnostic.corrupt;
        } else if (diagnostic.warmup < WARMUP_FRAMES) {
            ++diagnostic.warmup;
        } else {
            const int payload_error = frame_payload_valid(&frame);
            if (payload_error) {
                fail_once(payload_error, "frame-payload");
            } else {
                diagnostic.frame = frame;
                for (unsigned p = 0; p < frame.format.plane_count; ++p) {
                    if (write_all(output_fd,
                                  frame.planes[p].data,
                                  frame.planes[p].size) < 0) {
                        fail_once(errno, "output-write");
                        break;
                    }
                }
                diagnostic.saved = !diagnostic.first_error;
            }
        }
        result = rtctrl_camera_release(camera, frame.token);
        if (result) {
            fail_once(-result, "release");
        }
    }
    start_cleanup();
    result = rtctrl_camera_close(camera);
    if (result) {
        fail_once(-result, "capture-close");
    }
    if (!diagnostic.first_error && !diagnostic.saved) {
        fail_once(ENODATA, "no-frame");
    }
    if (__wrap_close(output_fd) < 0) {
        diagnostic.output_close_errno = errno;
        fail_once(errno, "output-close");
    }
    /* Metadata records payload lengths only; no borrowed pixels are dereferenced
     * after frame release. A partial/error file is never described as success. */
    if (emit_json_fd(metadata_fd, capture_mode()) < 0) {
        fail_once(errno, "metadata-output");
    }
    if (__wrap_close(metadata_fd) < 0) {
        diagnostic.output_close_errno = errno;
        fail_once(errno, "metadata-close");
    }
}
int main(int argc, char** argv) {
    diagnostic.camera_fd = -1;
    const int inspect = argc == 3 && !strcmp(argv[1], "--inspect");
    const int inspect_media = argc == 3 && !strcmp(argv[1], "--inspect-media");
    const int inspect_subdev = argc == 3 && !strcmp(argv[1], "--inspect-subdev");
    const int capture_640 =
        argc == 4 && !strcmp(argv[1], "--capture-once-640x480-nv12");
    const int capture =
        capture_640 || (argc == 4 && !strcmp(argv[1], "--capture-once"));
    if ((!inspect && !inspect_media && !inspect_subdev && !capture) ||
        argv[2][0] != '/' ||
        (capture && (argv[3][0] != '/' || !strcmp(argv[2], argv[3])))) {
        return 2;
    }
    if (strlen(CAPTURE_SOURCE_SHA) != 64) {
        return 2;
    }
    diagnostic.device = argv[2];
    diagnostic.request_640_nv12 = capture_640;
    if (capture_640) {
        diagnostic.requested_native.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
        diagnostic.requested_native.fmt.pix_mp.width = 640;
        diagnostic.requested_native.fmt.pix_mp.height = 480;
        diagnostic.requested_native.fmt.pix_mp.pixelformat = V4L2_PIX_FMT_NV12;
        diagnostic.requested_native.fmt.pix_mp.field = V4L2_FIELD_NONE;
        diagnostic.requested_native.fmt.pix_mp.num_planes = 1;
    }
    diagnostic.inspect_media = inspect_media;
    diagnostic.inspect_subdev = inspect_subdev;
    diagnostic.started_ns = now_ns();
    if (diagnostic.started_ns < 0 || start_timer() < 0) {
        return 1;
    }
    diagnostic.deadline_ns = diagnostic.started_ns + MAIN_BUDGET_NS;
    if (inspect_media) {
        inspect_media_device();
    } else if (inspect_subdev) {
        inspect_subdev_device();
    } else if (!inspect_device() && capture) {
        capture_once(argv[3]);
    }
    if (cleanup_uncertain()) {
        fail_once(EIO, "cleanup");
    }
    start_final_output();
    const char* mode = inspect          ? "inspect"
                       : inspect_media  ? "inspect-media"
                       : inspect_subdev ? "inspect-subdev"
                                        : capture_mode();
    if (emit_json_fd(STDOUT_FILENO, mode) < 0) {
        fail_once(errno, "stdout-output");
    }
    const struct itimerval disabled = {{0, 0}, {0, 0}};
    setitimer(ITIMER_REAL, &disabled, NULL);
    return diagnostic.first_error ? 1 : 0;
}
