#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/media.h>
#include <linux/v4l2-subdev.h>
#include <linux/videodev2.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <time.h>
#include <unistd.h>

int __real_open(const char*, int, ...);
int __real_close(int);
ssize_t __real_write(int, const void*, size_t);
int __real_poll(struct pollfd*, nfds_t, int);

static int64_t simulated_ns = INT64_C(1000000000);
static unsigned opens, maps, dq_count;
static unsigned sfmt_calls, verify_gfmt_calls, reqbuf_calls, streamon_calls;
static unsigned requested_field, requested_planes;
static int configured_ready;
static struct v4l2_format configured;
static void* allocations[32];
static size_t allocation_sizes[32];
static unsigned unmaps;
static int fixture_output_fd = -1;
static int fixture_metadata_fd = -1;
static unsigned file_creates;
static const char* scenario(void) {
    const char* value = getenv("CAMERA_FIXTURE");
    return value ? value : "success";
}
static int is_case(const char* value) {
    return !strcmp(scenario(), value);
}
int64_t fixture_now_ns(void) {
#ifdef CAPTURE_REAL_TIME_FIXTURE
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) < 0) {
        return -1;
    }
    return (int64_t)now.tv_sec * INT64_C(1000000000) + now.tv_nsec;
#else
    simulated_ns += 100000;
    return simulated_ns;
#endif
}
int fixture_open(const char* path, int flags, mode_t mode) {
    if (!strcmp(path, "/dev/video-fixture") || !strcmp(path, "/dev/media-fixture") ||
        !strcmp(path, "/dev/subdev-fixture")) {
        return 900 + (int)++opens;
    }
    const int fd = __real_open(path, flags, mode);
    if ((flags & O_CREAT) && fd >= 0) {
        ++file_creates;
        if (file_creates == 1) {
            fixture_output_fd = fd;
        } else if (file_creates == 2) {
            fixture_metadata_fd = fd;
        }
    }
    return fd;
}
int fixture_fstat(int fd, struct stat* identity) {
    if (fd >= 901 && fd <= 902) {
        memset(identity, 0, sizeof(*identity));
        identity->st_mode = S_IFCHR | 0600;
        identity->st_dev = 2;
        identity->st_rdev = makedev(81, 7);
        identity->st_ino = 49 + (is_case("identity-drift") && fd == 902);
        return 0;
    }
    return fstat(fd, identity);
}
static void trace_fixture(void) {
    const char* path = getenv("CAMERA_TRACE");
    if (!path) {
        return;
    }
    char json[512];
    const int size =
        snprintf(json,
                 sizeof(json),
                 "{\"sfmt\":%u,\"gfmt_verify\":%u,\"reqbufs\":%u,\"streamon\":%u,"
                 "\"requested_field\":%u,\"requested_planes\":%u}\n",
                 sfmt_calls,
                 verify_gfmt_calls,
                 reqbuf_calls,
                 streamon_calls,
                 requested_field,
                 requested_planes);
    const int fd = __real_open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (fd >= 0) {
        if (size > 0 && (size_t)size < sizeof(json)) {
            (void)__real_write(fd, json, (size_t)size);
        }
        (void)__real_close(fd);
    }
}
int fixture_close(int fd) {
    if (fd == 902) {
        trace_fixture();
    }
    if (fd >= 901 && fd <= 902) {
        if ((fd == 902 && is_case("close-error")) ||
            is_case("inspect-close-error")) {
            errno = EIO;
            return -1;
        }
        return 0;
    }
    const int result = __real_close(fd);
    if (fd == fixture_output_fd && is_case("output-close-error")) {
        errno = EIO;
        return -1;
    }
    return result;
}
static unsigned plane_count(void) {
    return configured_ready ? configured.fmt.pix_mp.num_planes
                            : (is_case("nv12m") ? 2 : 1);
}
static unsigned payload_size(unsigned plane) {
    return configured_ready ? configured.fmt.pix_mp.plane_fmt[plane].sizeimage
                            : (is_case("nv12m") ? (plane ? 1536 : 3072) : 4608);
}
int fixture_ioctl(int fd, unsigned long request, void* argument) {
    simulated_ns += 100000;
    if (fd < 901 || fd > 902) {
        errno = ENOTTY;
        return -1;
    }
    if (fd == 902 && is_case("blocking-startup-eintr")) {
        pause();
        errno = EINTR;
        return -1;
    }
    if (fd == 902 && is_case("startup-eintr")) {
        simulated_ns += 1000000;
        errno = EINTR;
        return -1;
    }
    if (request == MEDIA_IOC_DEVICE_INFO) {
        struct media_device_info* info = argument;
        memset(info, 0, sizeof(*info));
        strcpy(info->driver, "fixture-media");
        strcpy(info->model, "OV5695 + ISP");
        strcpy(info->bus_info, "platform:fixture");
        info->media_version = 0x050a00;
        return 0;
    }
    if (request == MEDIA_IOC_ENUM_ENTITIES) {
        struct media_entity_desc* entity = argument;
        const unsigned previous = entity->id & ~MEDIA_ENT_ID_FLAG_NEXT;
        if (is_case("media-empty") ||
            ((is_case("media-max-entities") || is_case("media-max-width")) &&
             previous >= 256) ||
            (previous >= 2 && !is_case("media-overflow") &&
             !is_case("media-max-entities") && !is_case("media-max-width"))) {
            errno = EINVAL;
            return -1;
        }
        memset(entity, 0, sizeof(*entity));
        entity->id =
            is_case("media-duplicate") && previous ? previous : previous + 1;
        snprintf(entity->name, sizeof(entity->name), "entity-%u", entity->id);
        entity->type = MEDIA_ENT_F_CAM_SENSOR;
        entity->pads = is_case("media-pad-overflow") ? 33
                       : is_case("media-max-width")  ? 32
                                                     : 1;
        entity->links = is_case("media-link-overflow") ? 65
                        : is_case("media-max-width")   ? 64
                                                       : 1;
        entity->dev.major = 81;
        entity->dev.minor = entity->id;
        return 0;
    }
    if (request == MEDIA_IOC_ENUM_LINKS) {
        struct media_links_enum* links = argument;
        if (is_case("media-links-error")) {
            errno = EIO;
            return -1;
        }
        if (is_case("media-max-width")) {
            for (unsigned p = 0; p < 32; ++p) {
                links->pads[p].entity = links->entity;
                links->pads[p].index = p;
                links->pads[p].flags = MEDIA_PAD_FL_SOURCE;
            }
            for (unsigned l = 0; l < 64; ++l) {
                links->links[l].source.entity = links->entity;
                links->links[l].source.index = l % 32;
                links->links[l].sink.entity =
                    links->entity < 256 ? links->entity + 1 : links->entity;
                links->links[l].sink.index = (l + 1) % 32;
                links->links[l].flags = MEDIA_LNK_FL_ENABLED;
            }
            return 0;
        }
        links->pads[0].entity = links->entity;
        links->pads[0].index = is_case("media-invalid-pad") ? 1 : 0;
        links->pads[0].flags =
            links->entity == 1 ? MEDIA_PAD_FL_SOURCE : MEDIA_PAD_FL_SINK;
        links->links[0].source.entity = links->entity == 1 ? 1 : links->entity - 1;
        links->links[0].source.index = 0;
        links->links[0].sink.entity = links->entity == 1 ? 2 : links->entity;
        links->links[0].sink.index = 0;
        links->links[0].flags = MEDIA_LNK_FL_ENABLED | MEDIA_LNK_FL_IMMUTABLE;
        if (is_case("media-invalid-link")) {
            links->links[0].source.entity = 99;
            links->links[0].sink.entity = 100;
        }
        return 0;
    }
    if (request == VIDIOC_SUBDEV_G_FMT) {
        struct v4l2_subdev_format* format = argument;
        if (is_case("subdev-empty") ||
            (format->pad >= 2 && !is_case("subdev-eight-pads"))) {
            errno = EINVAL;
            return -1;
        }
        if (is_case("subdev-format-error")) {
            errno = EIO;
            return -1;
        }
        format->format.width = 1920;
        format->format.height = 1080;
        format->format.code = 0x300a;
        if (is_case("subdev-invalid-format")) {
            format->format.code = 0;
        }
        return 0;
    }
    if (request == VIDIOC_SUBDEV_ENUM_MBUS_CODE) {
        struct v4l2_subdev_mbus_code_enum* code = argument;
        if ((is_case("subdev-code-max") && code->index >= 32) ||
            (code->index >= 2 && !is_case("subdev-code-overflow") &&
             !is_case("subdev-code-max"))) {
            errno = EINVAL;
            return -1;
        }
        if (is_case("subdev-code-error")) {
            errno = EIO;
            return -1;
        }
        code->code = 0x300a + code->index;
        return 0;
    }
    if (request == VIDIOC_QUERYCAP) {
        struct v4l2_capability* cap = argument;
        memset(cap, 0, sizeof(*cap));
        strcpy((char*)cap->driver, "fixture-rkisp");
        strcpy((char*)cap->card, "fixture \"camera\"\n");
        strcpy((char*)cap->bus_info, "platform:fixture");
        cap->capabilities = V4L2_CAP_DEVICE_CAPS;
        cap->device_caps = V4L2_CAP_VIDEO_CAPTURE_MPLANE | V4L2_CAP_STREAMING;
        if (is_case("wrong-capability")) {
            cap->device_caps = V4L2_CAP_META_CAPTURE;
        }
        if (is_case("capability-drift") && fd == 902) {
            strcpy((char*)cap->driver, "wrong");
        }
        return 0;
    }
    if (request == VIDIOC_S_FMT) {
        struct v4l2_format* fmt = argument;
        ++sfmt_calls;
        requested_field = fmt->fmt.pix_mp.field;
        requested_planes = fmt->fmt.pix_mp.num_planes;
        if (is_case("format-sfmt-blocking")) {
            pause();
            errno = EINTR;
            return -1;
        }
        if (is_case("format-sfmt-eintr")) {
            simulated_ns += 1000000;
            errno = EINTR;
            return -1;
        }
        if (is_case("format-sfmt-cancel")) {
            raise(SIGTERM);
            errno = EINTR;
            return -1;
        }
        if (is_case("format-sfmt-error")) {
            errno = EIO;
            return -1;
        }
        memset(fmt, 0, sizeof(*fmt));
        fmt->type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
        fmt->fmt.pix_mp.width = 640;
        fmt->fmt.pix_mp.height = 480;
        fmt->fmt.pix_mp.pixelformat = V4L2_PIX_FMT_NV12;
        fmt->fmt.pix_mp.field = V4L2_FIELD_NONE;
        fmt->fmt.pix_mp.num_planes = 1;
        fmt->fmt.pix_mp.colorspace = V4L2_COLORSPACE_SRGB;
        fmt->fmt.pix_mp.quantization = V4L2_QUANTIZATION_LIM_RANGE;
        fmt->fmt.pix_mp.xfer_func = V4L2_XFER_FUNC_SRGB;
        fmt->fmt.pix_mp.ycbcr_enc = V4L2_YCBCR_ENC_601;
        fmt->fmt.pix_mp.plane_fmt[0].bytesperline =
            is_case("format-padded") ? 672 : 640;
        fmt->fmt.pix_mp.plane_fmt[0].sizeimage =
            fmt->fmt.pix_mp.plane_fmt[0].bytesperline * 480U * 3U / 2U;
        if (is_case("format-width-adjust"))
            fmt->fmt.pix_mp.width = 638;
        if (is_case("format-height-adjust"))
            fmt->fmt.pix_mp.height = 478;
        if (is_case("format-fourcc-adjust"))
            fmt->fmt.pix_mp.pixelformat = V4L2_PIX_FMT_NV21;
        if (is_case("format-plane-zero"))
            fmt->fmt.pix_mp.num_planes = 0;
        if (is_case("format-plane-two"))
            fmt->fmt.pix_mp.num_planes = 2;
        if (is_case("format-field-adjust"))
            fmt->fmt.pix_mp.field = V4L2_FIELD_INTERLACED;
        if (is_case("format-type-adjust"))
            fmt->type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        if (is_case("format-bad-stride"))
            fmt->fmt.pix_mp.plane_fmt[0].bytesperline = 639;
        if (is_case("format-size-zero"))
            fmt->fmt.pix_mp.plane_fmt[0].sizeimage = 0;
        if (is_case("format-size-short"))
            --fmt->fmt.pix_mp.plane_fmt[0].sizeimage;
        if (is_case("format-size-overflow"))
            fmt->fmt.pix_mp.plane_fmt[0].sizeimage = UINT32_MAX;
        configured = *fmt;
        configured_ready = 1;
        return 0;
    }
    if (request == VIDIOC_G_FMT && configured_ready) {
        ++verify_gfmt_calls;
        if (is_case("format-gfmt-blocking")) {
            pause();
            errno = EINTR;
            return -1;
        }
        if (is_case("format-gfmt-eintr")) {
            simulated_ns += 1000000;
            errno = EINTR;
            return -1;
        }
        if (is_case("format-gfmt-error")) {
            errno = EIO;
            return -1;
        }
        *(struct v4l2_format*)argument = configured;
        struct v4l2_format* fmt = argument;
        if (is_case("format-gfmt-width-drift"))
            --fmt->fmt.pix_mp.width;
        if (is_case("format-gfmt-color-drift"))
            fmt->fmt.pix_mp.quantization = V4L2_QUANTIZATION_FULL_RANGE;
        if (is_case("format-gfmt-stride-drift"))
            ++fmt->fmt.pix_mp.plane_fmt[0].bytesperline;
        if (is_case("format-gfmt-type-drift"))
            fmt->type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        return 0;
    }
    if (request == VIDIOC_G_FMT) {
        struct v4l2_format* fmt = argument;
        memset(&fmt->fmt.pix_mp, 0, sizeof(fmt->fmt.pix_mp));
        fmt->fmt.pix_mp.width = !strncmp(scenario(), "format-", 7) ? 2592 : 64;
        fmt->fmt.pix_mp.height = !strncmp(scenario(), "format-", 7) ? 1944 : 48;
        fmt->fmt.pix_mp.pixelformat =
            is_case("nv12m") ? V4L2_PIX_FMT_NV12M : V4L2_PIX_FMT_NV12;
        fmt->fmt.pix_mp.num_planes = plane_count();
        fmt->fmt.pix_mp.colorspace = V4L2_COLORSPACE_SRGB;
        fmt->fmt.pix_mp.quantization = V4L2_QUANTIZATION_LIM_RANGE;
        fmt->fmt.pix_mp.xfer_func = V4L2_XFER_FUNC_SRGB;
        fmt->fmt.pix_mp.ycbcr_enc = V4L2_YCBCR_ENC_601;
        for (unsigned p = 0; p < plane_count(); ++p) {
            fmt->fmt.pix_mp.plane_fmt[p].bytesperline = 64;
            fmt->fmt.pix_mp.plane_fmt[p].sizeimage = payload_size(p);
        }
        if (is_case("format-drift") && fd == 902) {
            fmt->fmt.pix_mp.height = 46;
        }
        if (is_case("invalid-dimensions")) {
            fmt->fmt.pix_mp.width = 0;
        }
        if (is_case("unknown-format")) {
            fmt->fmt.pix_mp.pixelformat = V4L2_PIX_FMT_SBGGR10;
        }
        if (is_case("bad-stride")) {
            fmt->fmt.pix_mp.plane_fmt[0].bytesperline = 63;
        }
        if (!strncmp(scenario(), "format-", 7)) {
            fmt->fmt.pix_mp.field = V4L2_FIELD_NONE;
            fmt->fmt.pix_mp.plane_fmt[0].bytesperline = 2592;
            fmt->fmt.pix_mp.plane_fmt[0].sizeimage = 2592U * 1944U * 3U / 2U;
        }
        if (is_case("format-initial-raw")) {
            fmt->fmt.pix_mp.pixelformat = V4L2_PIX_FMT_SBGGR10;
            fmt->fmt.pix_mp.plane_fmt[0].bytesperline = 5184;
            fmt->fmt.pix_mp.plane_fmt[0].sizeimage = 5184U * 1944U;
        }
        return 0;
    }
    if (request == VIDIOC_ENUM_FMT) {
        struct v4l2_fmtdesc* fmt = argument;
        if ((is_case("enum-max") && fmt->index >= 64) ||
            (fmt->index && !is_case("enum-overflow") && !is_case("enum-max"))) {
            errno = EINVAL;
            return -1;
        }
        fmt->pixelformat = V4L2_PIX_FMT_NV12;
        strcpy((char*)fmt->description, "NV12");
        return 0;
    }
    if (request == VIDIOC_REQBUFS) {
        if (((struct v4l2_requestbuffers*)argument)->count)
            ++reqbuf_calls;
        return 0;
    }
    if (request == VIDIOC_QUERYBUF) {
        struct v4l2_buffer* buf = argument;
        if (is_case("partial-query-error") && buf->index == 2) {
            errno = EIO;
            return -1;
        }
        for (unsigned p = 0; p < plane_count(); ++p) {
            buf->m.planes[p].length = payload_size(p);
            buf->m.planes[p].m.mem_offset = buf->index * 65536 + p * 4096;
        }
        return 0;
    }
    if (request == VIDIOC_QBUF) {
        if (dq_count >= 3 && is_case("release-error")) {
            errno = EIO;
            return -1;
        }
        return 0;
    }
    if (request == VIDIOC_STREAMON) {
        ++streamon_calls;
        if (is_case("streamon-error")) {
            errno = EIO;
            return -1;
        }
        return 0;
    }
    if (request == VIDIOC_STREAMOFF) {
        if (is_case("blocking-streamoff-eintr")) {
            pause();
            errno = EINTR;
            return -1;
        }
        if (is_case("streamoff-error") || is_case("combined-cleanup-error")) {
            errno = EIO;
            return -1;
        }
        if (is_case("streamoff-eintr")) {
            simulated_ns += 1000000;
            errno = EINTR;
            return -1;
        }
        return 0;
    }
    if (request == VIDIOC_DQBUF) {
        if (is_case("dq-eagain")) {
            simulated_ns += 1000000;
            errno = EAGAIN;
            return -1;
        }
        if (is_case("dq-eintr")) {
            simulated_ns += 1000000;
            errno = EINTR;
            return -1;
        }
        struct v4l2_buffer* buf = argument;
        ++dq_count;
        buf->index = is_case("bad-index") ? 5 : 0;
        buf->sequence = dq_count;
        buf->timestamp.tv_sec = 1;
        buf->flags = V4L2_BUF_FLAG_TIMESTAMP_MONOTONIC;
        if (is_case("all-corrupt")) {
            buf->flags |= V4L2_BUF_FLAG_ERROR;
            simulated_ns += 1000000;
        }
        for (unsigned p = 0; p < plane_count(); ++p) {
            buf->m.planes[p].bytesused = payload_size(p);
            buf->m.planes[p].data_offset = 0;
        }
        if (is_case("zero-payload")) {
            buf->m.planes[0].bytesused = 0;
        }
        if (is_case("short-payload")) {
            --buf->m.planes[0].bytesused;
        }
        if (is_case("oversize-payload")) {
            ++buf->m.planes[0].bytesused;
        }
        if (is_case("bad-offset")) {
            buf->m.planes[0].data_offset = buf->m.planes[0].bytesused + 1;
        }
        if (is_case("offset-payload")) {
            buf->m.planes[0].data_offset = 16;
        }
        return 0;
    }
    errno = EINVAL;
    return -1;
}
void* fixture_mmap(
    void* address, size_t length, int protection, int flags, int fd, off_t offset) {
    (void)address;
    (void)protection;
    (void)flags;
    (void)fd;
    (void)offset;
    if (is_case("partial-mmap-error") && maps == 2) {
        errno = ENOMEM;
        return MAP_FAILED;
    }
    if (maps >= 32) {
        errno = ENOMEM;
        return MAP_FAILED;
    }
    void* value = malloc(length);
    if (!value) {
        return MAP_FAILED;
    }
    memset(value, 42 + maps, length);
    allocations[maps] = value;
    allocation_sizes[maps] = length;
    ++maps;
    return value;
}
int fixture_munmap(void* address, size_t length) {
    for (unsigned i = 0; i < maps; ++i) {
        if (allocations[i] == address && allocation_sizes[i] == length) {
            free(address);
            allocations[i] = NULL;
            ++unmaps;
            if (unmaps == 1 &&
                (is_case("munmap-error") || is_case("combined-cleanup-error"))) {
                errno = EIO;
                return -1;
            }
            return 0;
        }
    }
    errno = EINVAL;
    return -1;
}
int fixture_poll(struct pollfd* descriptors, nfds_t count, int timeout) {
    if (descriptors[0].fd < 901 || descriptors[0].fd > 902) {
        struct timespec before, after;
        clock_gettime(CLOCK_MONOTONIC, &before);
        const int result = __real_poll(descriptors, count, timeout);
        const int error = errno;
        clock_gettime(CLOCK_MONOTONIC, &after);
        const int64_t elapsed =
            (int64_t)(after.tv_sec - before.tv_sec) * INT64_C(1000000000) +
            after.tv_nsec - before.tv_nsec;
        /* Account actual waits including EINTR; ready output pipes are not
         * charged the whole requested timeout. */
        simulated_ns += elapsed > 100000 ? elapsed : 100000;
        errno = error;
        return result;
    }
    simulated_ns += (int64_t)timeout * 1000000;
    if (is_case("poll-timeout")) {
        return 0;
    }
    if (is_case("cancel-before-frame")) {
        raise(SIGTERM);
        errno = EINTR;
        return -1;
    }
    if (is_case("poll-eintr")) {
        errno = EINTR;
        return -1;
    }
    if (is_case("poll-error")) {
        descriptors[0].revents = POLLERR;
        return 1;
    }
    descriptors[0].revents = POLLIN;
    return 1;
}

ssize_t fixture_write(int fd, const void* data, size_t size) {
    if ((fd == fixture_output_fd && is_case("output-write-error")) ||
        (fd == fixture_metadata_fd && is_case("metadata-write-error"))) {
        errno = ENOSPC;
        return -1;
    }
    if ((fd == fixture_output_fd && is_case("output-write-zero")) ||
        (fd == fixture_metadata_fd && is_case("metadata-write-zero"))) {
        return 0;
    }
    if ((fd == fixture_output_fd && is_case("output-write-eintr")) ||
        (fd == fixture_metadata_fd && is_case("metadata-write-eintr"))) {
        simulated_ns += 1000000;
        errno = EINTR;
        return -1;
    }
    return __real_write(fd, data, size);
}
