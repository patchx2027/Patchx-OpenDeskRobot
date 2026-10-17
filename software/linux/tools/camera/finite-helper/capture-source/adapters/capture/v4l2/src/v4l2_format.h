#ifndef RTCTRL_PRIVATE_V4L2_FORMAT_H
#define RTCTRL_PRIVATE_V4L2_FORMAT_H
#include "rtctrl/capture/capture.h"
#include <linux/videodev2.h>
void rtctrl_v4l2_translate_format(const struct v4l2_pix_format_mplane* native,
                                  struct rtctrl_camera_format* output);
#endif
