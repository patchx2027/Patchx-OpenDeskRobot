#ifndef RTCTRL_ADAPTERS_V4L2_CAPTURE_H
#define RTCTRL_ADAPTERS_V4L2_CAPTURE_H
#include "rtctrl/capture/capture_backend.h"
#ifdef __cplusplus
extern "C" {
#endif
/* V4L2-only configuration. Both dimensions zero preserve the device format. */
struct rtctrl_v4l2_config {
    const char* device;
    uint32_t width;
    uint32_t height;
    uint32_t fourcc;
    uint32_t buffer_count;
    int export_dmabuf; /* Zero: MMAP only. Nonzero: require DMA-BUF export. */
};
const struct rtctrl_capture_backend* rtctrl_v4l2_backend(void);
int rtctrl_v4l2_open(const struct rtctrl_v4l2_config* config,
                     struct rtctrl_camera** camera);
#ifdef __cplusplus
}
#endif
#endif
