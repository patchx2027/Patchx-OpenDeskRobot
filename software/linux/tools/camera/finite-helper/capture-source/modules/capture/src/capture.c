#include "rtctrl/capture/capture_backend.h"
#include <errno.h>
#include <stdlib.h>

struct rtctrl_camera {
    struct rtctrl_capture_backend backend;
    void* context;
    uint64_t token; // token相当于票号，这样能够区分不同生命周期的资源
    uint64_t backend_token;
    int held;
    int failed;
};

static int valid_format(const struct rtctrl_camera_format* f) {
    return f->width && f->height && f->plane_count &&
           f->plane_count <= RTCTRL_CAMERA_MAX_PLANES &&
           f->pixel_format <= RTCTRL_PIXEL_UYVY &&
           f->colorspace <= RTCTRL_COLOR_RAW &&
           f->quantization <= RTCTRL_RANGE_FULL &&
           f->transfer_function <= RTCTRL_TRANSFER_SMPTE2084 &&
           f->ycbcr_encoding <= RTCTRL_YCBCR_XV709;
}

int rtctrl_camera_create(const struct rtctrl_capture_backend* backend,
                         const void* config,
                         struct rtctrl_camera** output) {
    if (!output) {
        return -EINVAL;
    }
    *output = NULL;
    if (!backend || backend->version != 1 || !backend->open || !backend->close ||
        !backend->get_format || !backend->acquire || !backend->release) {
        return -EINVAL;
    }
    struct rtctrl_camera* c = calloc(1, sizeof(*c));
    if (!c) {
        return -ENOMEM;
    }
    c->backend = *backend;
    int result = c->backend.open(config, &c->context);
    if (result > 0 || (!result && !c->context)) {
        result = -EPROTO;
    }
    if (!result) {
        struct rtctrl_camera_format format;
        result = rtctrl_camera_get_format(c, &format);
    }
    if (result) {
        (void)rtctrl_camera_close(c);
        return result;
    }
    *output = c;
    return 0;
}

int rtctrl_camera_get_format(const struct rtctrl_camera* c,
                             struct rtctrl_camera_format* output) {
    if (!c || !output) {
        return -EINVAL;
    }
    if (c->failed) {
        return -EIO;
    }
    struct rtctrl_camera_format format = {0};
    const int result = c->backend.get_format(c->context, &format);
    if (result) {
        return result < 0 ? result : -EPROTO;
    }
    if (!valid_format(&format)) {
        return -EPROTO;
    }
    *output = format;
    return 0;
}

int rtctrl_camera_acquire(struct rtctrl_camera* c,
                          int timeout_ms,
                          struct rtctrl_camera_frame* output) {
    if (!c || !output || timeout_ms < 0) {
        return -EINVAL;
    }
    if (c->failed) {
        return -EIO;
    }
    if (c->held) {
        return -EBUSY;
    }
    if (c->token == UINT64_MAX) {
        c->failed = 1;
        return -EOVERFLOW;
    }
    struct rtctrl_camera_frame frame = {0};
    const int result = c->backend.acquire(c->context, timeout_ms, &frame);
    if (result) {
        if (result != -EAGAIN && result != -EINTR && result != -ETIMEDOUT &&
            result != -EBUSY) {
            c->failed = 1;
        }
        return result < 0 ? result : -EPROTO;
    }
    int valid = valid_format(&frame.format) && frame.timestamp_ns >= 0 &&
                frame.dequeued_time_ns >= 0;
    if (valid) {
        for (uint32_t p = 0; p < frame.format.plane_count; ++p) {
            if (frame.planes[p].size && !frame.planes[p].data) {
                valid = 0;
            }
        }
    }
    if (!valid) {
        (void)c->backend.release(c->context, frame.token);
        c->failed = 1;
        return -EPROTO;
    }
    c->backend_token = frame.token;
    frame.token = ++c->token;
    c->held = 1;
    *output = frame;
    return 0;
}

int rtctrl_camera_release(struct rtctrl_camera* c, uint64_t token) {
    if (!c || !c->held || token != c->token) {
        return -EINVAL;
    }
    const int result = c->backend.release(c->context, c->backend_token);
    c->held = 0;
    if (result) {
        c->failed = 1;
    }
    return result <= 0 ? result : -EPROTO;
}

int rtctrl_camera_close(struct rtctrl_camera* c) {
    if (!c) {
        return 0;
    }
    const int result = c->context ? c->backend.close(c->context) : 0;
    free(c);
    return result <= 0 ? result : -EPROTO;
}
