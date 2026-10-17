#include "rtctrl/adapters/v4l2/capture.h"
#include "v4l2_format.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/videodev2.h>
#include <poll.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

struct mapping {
    void* data;
    size_t length;
    int dmabuf_fd;
};
struct camera_buffer {
    struct mapping planes[RTCTRL_CAMERA_MAX_PLANES];
};
struct v4l2_camera {
    int fd;
    int streaming;
    int held;
    int failed;
    uint32_t held_index;
    uint64_t token;
    uint32_t count;
    struct camera_buffer* buffers;
    struct rtctrl_camera_format format;
    uint32_t strides[RTCTRL_CAMERA_MAX_PLANES];
};

static int camera_ioctl(int fd, unsigned long request, void* arg) {
    int result;
    do {
        result = ioctl(fd, request, arg);
    } while (result < 0 && errno == EINTR);
    return result < 0 ? -errno : 0;
}

static void buffer_init(const struct v4l2_camera* c,
                        struct v4l2_buffer* b,
                        struct v4l2_plane* planes,
                        uint32_t index) {
    memset(b, 0, sizeof(*b));
    memset(planes, 0, sizeof(*planes) * VIDEO_MAX_PLANES);
    b->type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
    b->memory = V4L2_MEMORY_MMAP;
    b->index = index;
    b->length = c->format.plane_count;
    b->m.planes = planes;
}

static int v4l2_close(struct v4l2_camera* c) {
    if (!c) {
        return 0;
    }
    int result = 0;
    if (c->streaming) {
        enum v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
        result = camera_ioctl(c->fd, VIDIOC_STREAMOFF, &type);
    }
    if (c->buffers) {
        for (uint32_t i = 0; i < c->count; ++i) {
            for (uint32_t p = 0; p < c->format.plane_count; ++p) {
                struct mapping* m = &c->buffers[i].planes[p];
                if (m->dmabuf_fd >= 0 && close(m->dmabuf_fd) < 0 && !result) {
                    result = -errno;
                }
                if (m->length && munmap(m->data, m->length) < 0 && !result) {
                    result = -errno;
                }
            }
        }
        free(c->buffers);
    }
    if (c->fd >= 0) {
        /* Closing also releases any queued buffers after partial startup. */
        if (close(c->fd) < 0 && !result) {
            result = -errno;
        }
    }
    free(c);
    return result;
}

static int v4l2_open(const struct rtctrl_v4l2_config* cfg,
                     struct v4l2_camera** output) {
    if (!output) {
        return -EINVAL;
    }
    *output = NULL;
    if (!cfg || !cfg->device || cfg->buffer_count < 2 || cfg->buffer_count > 32 ||
        ((cfg->width || cfg->height) &&
         (!cfg->width || !cfg->height || !cfg->fourcc))) {
        return -EINVAL;
    }
    struct v4l2_camera* c = calloc(1, sizeof(*c));
    if (!c) {
        return -ENOMEM;
    }
    c->fd = open(cfg->device, O_RDWR | O_NONBLOCK | O_CLOEXEC);
    int result = c->fd < 0 ? -errno : 0;
    if (result) {
        goto fail;
    }
    struct v4l2_capability caps = {0};
    result = camera_ioctl(c->fd, VIDIOC_QUERYCAP, &caps);
    if (result) {
        goto fail;
    }
    const uint32_t flags = (caps.capabilities & V4L2_CAP_DEVICE_CAPS)
                               ? caps.device_caps
                               : caps.capabilities;
    if (!(flags & V4L2_CAP_VIDEO_CAPTURE_MPLANE) || !(flags & V4L2_CAP_STREAMING)) {
        result = -ENOTSUP;
        goto fail;
    }
    struct v4l2_format fmt = {0};
    fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
    if (cfg->width) {
        fmt.fmt.pix_mp.width = cfg->width;
        fmt.fmt.pix_mp.height = cfg->height;
        fmt.fmt.pix_mp.pixelformat = cfg->fourcc;
        fmt.fmt.pix_mp.field = V4L2_FIELD_ANY;
    }
    result = camera_ioctl(c->fd, cfg->width ? VIDIOC_S_FMT : VIDIOC_G_FMT, &fmt);
    if (result) {
        goto fail;
    }
    const struct v4l2_pix_format_mplane* f = &fmt.fmt.pix_mp;
    if (!f->width || !f->height || !f->num_planes ||
        f->num_planes > RTCTRL_CAMERA_MAX_PLANES) {
        result = -EPROTO;
        goto fail;
    }
    rtctrl_v4l2_translate_format(f, &c->format);
    for (uint32_t p = 0; p < c->format.plane_count; ++p) {
        c->strides[p] = f->plane_fmt[p].bytesperline;
    }
    struct v4l2_requestbuffers req = {0};
    req.count = cfg->buffer_count;
    req.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
    req.memory = V4L2_MEMORY_MMAP;
    result = camera_ioctl(c->fd, VIDIOC_REQBUFS, &req);
    if (result) {
        goto fail;
    }
    if (req.count < 2 || req.count > 32) {
        result = -ENOBUFS;
        goto fail;
    }
    c->count = req.count;
    c->buffers = calloc(c->count, sizeof(*c->buffers));
    if (!c->buffers) {
        result = -ENOMEM;
        goto fail;
    }
    /* Initialize every descriptor before any partial startup can fail. */
    for (uint32_t i = 0; i < c->count; ++i) {
        for (uint32_t p = 0; p < c->format.plane_count; ++p) {
            c->buffers[i].planes[p].dmabuf_fd = -1;
        }
    }
    for (uint32_t i = 0; i < c->count; ++i) {
        struct v4l2_buffer b;
        struct v4l2_plane planes[VIDEO_MAX_PLANES];
        buffer_init(c, &b, planes, i);
        result = camera_ioctl(c->fd, VIDIOC_QUERYBUF, &b);
        if (result) {
            goto fail;
        }
        if (b.length != c->format.plane_count) {
            result = -EPROTO;
            goto fail;
        }
        for (uint32_t p = 0; p < c->format.plane_count; ++p) {
            if (!planes[p].length) {
                result = -EPROTO;
                goto fail;
            }
            void* data = mmap(NULL,
                              planes[p].length,
                              PROT_READ | PROT_WRITE,
                              MAP_SHARED,
                              c->fd,
                              planes[p].m.mem_offset);
            if (data == MAP_FAILED) {
                result = -errno;
                goto fail;
            }
            struct mapping* mapping = &c->buffers[i].planes[p];
            mapping->data = data;
            mapping->length = planes[p].length;
            if (cfg->export_dmabuf) {
                struct v4l2_exportbuffer exported = {0};
                exported.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
                exported.index = i;
                exported.plane = p;
                exported.flags = O_CLOEXEC | O_RDWR;
                exported.fd = -1;
                result = camera_ioctl(c->fd, VIDIOC_EXPBUF, &exported);
                if (result) {
                    goto fail;
                }
                if (exported.fd < 0) {
                    result = -EPROTO;
                    goto fail;
                }
                mapping->dmabuf_fd = exported.fd;
            }
        }
        result = camera_ioctl(c->fd, VIDIOC_QBUF, &b);
        if (result) {
            goto fail;
        }
    }
    enum v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
    result = camera_ioctl(c->fd, VIDIOC_STREAMON, &type);
    if (result) {
        goto fail;
    }
    c->streaming = 1;
    *output = c;
    return 0;
fail:
    (void)v4l2_close(c);
    return result;
}

static int v4l2_get_format(const struct v4l2_camera* c,
                           struct rtctrl_camera_format* f) {
    if (!c || !f) {
        return -EINVAL;
    }
    *f = c->format;
    return 0;
}

static int v4l2_acquire(struct v4l2_camera* c,
                        int timeout_ms,
                        struct rtctrl_camera_frame* frame) {
    if (!c || !frame || timeout_ms < 0) {
        return -EINVAL;
    }
    if (c->failed) {
        return -EIO;
    }
    if (c->held) {
        return -EBUSY;
    }
    struct pollfd descriptor = {c->fd, POLLIN, 0};
    const int ready = poll(&descriptor, 1, timeout_ms);
    if (ready < 0) {
        return -errno;
    }
    if (!ready) {
        return -ETIMEDOUT;
    }
    if (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL)) {
        c->failed = 1;
        return -EIO;
    }
    if (!(descriptor.revents & POLLIN)) {
        return -EAGAIN;
    }
    struct v4l2_buffer b;
    struct v4l2_plane planes[VIDEO_MAX_PLANES];
    buffer_init(c, &b, planes, 0);
    const int result = camera_ioctl(c->fd, VIDIOC_DQBUF, &b);
    if (result) {
        return result;
    }
    c->failed = 1; /* Remains poisoned if driver metadata is invalid. */
    if (b.index >= c->count || b.length != c->format.plane_count ||
        c->token == UINT64_MAX) {
        return -EPROTO;
    }
    memset(frame, 0, sizeof(*frame));
    frame->format = c->format;
    for (uint32_t p = 0; p < b.length; ++p) {
        if (planes[p].bytesused > c->buffers[b.index].planes[p].length ||
            planes[p].data_offset > planes[p].bytesused) {
            return -EPROTO;
        }
        frame->planes[p].data =
            (const unsigned char*)c->buffers[b.index].planes[p].data +
            planes[p].data_offset;
        frame->planes[p].size = planes[p].bytesused - planes[p].data_offset;
        frame->planes[p].stride = c->strides[p];
        frame->planes[p].dmabuf_fd = c->buffers[b.index].planes[p].dmabuf_fd;
        frame->planes[p].dmabuf_valid = frame->planes[p].dmabuf_fd >= 0;
        frame->planes[p].allocation_size = c->buffers[b.index].planes[p].length;
        frame->planes[p].data_offset = planes[p].data_offset;
    }
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) < 0) {
        return -errno;
    }
    if (b.timestamp.tv_sec < 0 ||
        b.timestamp.tv_sec > (INT64_MAX - 999999999) / 1000000000 ||
        b.timestamp.tv_usec < 0 || b.timestamp.tv_usec >= 1000000) {
        return -EPROTO;
    }
    frame->timestamp_ns =
        (int64_t)b.timestamp.tv_sec * 1000000000 + b.timestamp.tv_usec * 1000;
    frame->dequeued_time_ns = (int64_t)now.tv_sec * 1000000000 + now.tv_nsec;
    frame->timestamp_monotonic = (b.flags & V4L2_BUF_FLAG_TIMESTAMP_MASK) ==
                                 V4L2_BUF_FLAG_TIMESTAMP_MONOTONIC;
    frame->corrupt = !!(b.flags & V4L2_BUF_FLAG_ERROR);
    frame->sequence = b.sequence;
    frame->token = ++c->token;
    c->held = 1;
    c->held_index = b.index;
    c->failed = 0;
    return 0;
}

static int v4l2_release(struct v4l2_camera* c, uint64_t token) {
    if (!c || !c->held || token != c->token) {
        return -EINVAL;
    }
    struct v4l2_buffer b;
    struct v4l2_plane planes[VIDEO_MAX_PLANES];
    buffer_init(c, &b, planes, c->held_index);
    int result = camera_ioctl(c->fd, VIDIOC_QBUF, &b);
    c->held = 0;
    if (result) {
        c->failed = 1;
    }
    return result;
}

static int backend_open(const void* config, void** context) {
    struct v4l2_camera* camera = NULL;
    int result = v4l2_open(config, &camera);
    *context = camera;
    return result;
}
static int backend_format(const void* context, struct rtctrl_camera_format* format) {
    return v4l2_get_format(context, format);
}
static int
backend_acquire(void* context, int timeout_ms, struct rtctrl_camera_frame* frame) {
    return v4l2_acquire(context, timeout_ms, frame);
}
static int backend_release(void* context, uint64_t token) {
    return v4l2_release(context, token);
}
static int backend_close(void* context) {
    return v4l2_close(context);
}
const struct rtctrl_capture_backend* rtctrl_v4l2_backend(void) {
    static const struct rtctrl_capture_backend backend = {1,
                                                          backend_open,
                                                          backend_format,
                                                          backend_acquire,
                                                          backend_release,
                                                          backend_close};
    return &backend;
}
int rtctrl_v4l2_open(const struct rtctrl_v4l2_config* config,
                     struct rtctrl_camera** camera) {
    return rtctrl_camera_create(rtctrl_v4l2_backend(), config, camera);
}
