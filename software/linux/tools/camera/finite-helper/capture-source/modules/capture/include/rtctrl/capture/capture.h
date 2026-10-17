#ifndef RTCTRL_VISION_CAPTURE_H
#define RTCTRL_VISION_CAPTURE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define RTCTRL_CAMERA_MAX_PLANES 8

#include "rtctrl/capture/image_format.h"

/* Vendor-neutral borrowed frame contract. No V4L2 or RKAIQ types escape here. */
struct rtctrl_frame_plane {
    const void* data;
    size_t size;
    uint32_t stride;
    /* Optional borrowed DMA-BUF. Check dmabuf_valid before using fd (zero is a
     * valid descriptor). Do not close it. Device consumers must finish before
     * frame release; the exporter retains descriptor ownership. */
    int dmabuf_valid;
    int dmabuf_fd;
    size_t allocation_size; /* Full exported allocation, including data_offset. */
    size_t data_offset;     /* Payload offset in allocation; data points here. */
};
struct rtctrl_camera_format {
    uint32_t width;
    uint32_t height;
    uint32_t pixel_format; /* enum rtctrl_pixel_format */
    uint32_t
        native_format; /* Backend diagnostic value only; never use for processing. */
    uint32_t plane_count;
    uint32_t colorspace;        /* enum rtctrl_colorspace */
    uint32_t quantization;      /* enum rtctrl_quantization */
    uint32_t transfer_function; /* enum rtctrl_transfer_function */
    uint32_t ycbcr_encoding;    /* enum rtctrl_ycbcr_encoding */
};
struct rtctrl_camera_frame {
    struct rtctrl_camera_format format;
    struct rtctrl_frame_plane planes[RTCTRL_CAMERA_MAX_PLANES];
    uint64_t token;
    uint32_t sequence;
    int64_t timestamp_ns;
    int64_t dequeued_time_ns; /* Local monotonic time, or zero when unavailable. */
    /* A driver timestamp is not necessarily exposure start. */
    int timestamp_monotonic;
    int corrupt;
};
struct rtctrl_camera;

/* One owner thread per handle; no global device state. Negative errno on error.
 * open initializes and starts capture, unwinding every partial failure.
 * acquire allows one outstanding frame, valid until release or close.
 * Consumers must finish reading before release; asynchronous consumers need
 * a separate owned copy/pool. Errors other than EAGAIN/EINTR/ETIMEDOUT/EBUSY
 * require close and recreation. This API does not manage ISP/3A services. */
struct rtctrl_capture_backend;
/* Explicit composition: backend_config is consumed during creation. The backend
 * callbacks are copied, so the descriptor itself need not outlive this call. */
int rtctrl_camera_create(const struct rtctrl_capture_backend* backend,
                         const void* backend_config,
                         struct rtctrl_camera** camera);
int rtctrl_camera_get_format(const struct rtctrl_camera* camera,
                             struct rtctrl_camera_format* format);
int rtctrl_camera_acquire(struct rtctrl_camera* camera,
                          int timeout_ms,
                          struct rtctrl_camera_frame* frame);
int rtctrl_camera_release(struct rtctrl_camera* camera, uint64_t token);
/* Always destroys the handle, even when STREAMOFF fails. NULL is permitted. */
int rtctrl_camera_close(struct rtctrl_camera* camera);

#ifdef __cplusplus
}
#endif
#endif
