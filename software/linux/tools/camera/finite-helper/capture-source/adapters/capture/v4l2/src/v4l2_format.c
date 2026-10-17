#include "v4l2_format.h"

static uint32_t map_pixel(uint32_t value) {
    switch (value) {
        case V4L2_PIX_FMT_GREY:
            return RTCTRL_PIXEL_GRAY8;
        case V4L2_PIX_FMT_RGB24:
            return RTCTRL_PIXEL_RGB24;
        case V4L2_PIX_FMT_BGR24:
            return RTCTRL_PIXEL_BGR24;
        case V4L2_PIX_FMT_NV12:
            return RTCTRL_PIXEL_NV12;
        case V4L2_PIX_FMT_NV12M:
            return RTCTRL_PIXEL_NV12;
        case V4L2_PIX_FMT_NV21:
            return RTCTRL_PIXEL_NV21;
        case V4L2_PIX_FMT_NV21M:
            return RTCTRL_PIXEL_NV21;
        case V4L2_PIX_FMT_YUYV:
            return RTCTRL_PIXEL_YUYV;
        case V4L2_PIX_FMT_UYVY:
            return RTCTRL_PIXEL_UYVY;
        default:
            return RTCTRL_PIXEL_UNKNOWN;
    }
}

static uint32_t map_color(uint32_t value) {
    switch (value) {
        case V4L2_COLORSPACE_SRGB:
            return RTCTRL_COLOR_SRGB;
        case V4L2_COLORSPACE_JPEG:
            return RTCTRL_COLOR_SRGB;
        case V4L2_COLORSPACE_REC709:
            return RTCTRL_COLOR_REC709;
        case V4L2_COLORSPACE_BT2020:
            return RTCTRL_COLOR_BT2020;
        case V4L2_COLORSPACE_SMPTE170M:
            return RTCTRL_COLOR_SMPTE170M;
        case V4L2_COLORSPACE_SMPTE240M:
            return RTCTRL_COLOR_SMPTE240M;
        case V4L2_COLORSPACE_OPRGB:
            return RTCTRL_COLOR_OPRGB;
        case V4L2_COLORSPACE_DCI_P3:
            return RTCTRL_COLOR_DCI_P3;
        case V4L2_COLORSPACE_470_SYSTEM_M:
            return RTCTRL_COLOR_BT470_M;
        case V4L2_COLORSPACE_470_SYSTEM_BG:
            return RTCTRL_COLOR_BT470_BG;
        case V4L2_COLORSPACE_RAW:
            return RTCTRL_COLOR_RAW;
        default:
            return RTCTRL_COLOR_UNKNOWN;
    }
}

static uint32_t map_range(uint32_t value) {
    switch (value) {
        case V4L2_QUANTIZATION_FULL_RANGE:
            return RTCTRL_RANGE_FULL;
        case V4L2_QUANTIZATION_LIM_RANGE:
            return RTCTRL_RANGE_LIMITED;
        default:
            return RTCTRL_RANGE_UNKNOWN;
    }
}

static uint32_t map_xfer(uint32_t value) {
    switch (value) {
        case V4L2_XFER_FUNC_NONE:
            return RTCTRL_TRANSFER_LINEAR;
        case V4L2_XFER_FUNC_SRGB:
            return RTCTRL_TRANSFER_SRGB;
        case V4L2_XFER_FUNC_709:
            return RTCTRL_TRANSFER_REC709;
        case V4L2_XFER_FUNC_SMPTE240M:
            return RTCTRL_TRANSFER_SMPTE240M;
        case V4L2_XFER_FUNC_OPRGB:
            return RTCTRL_TRANSFER_OPRGB;
        case V4L2_XFER_FUNC_DCI_P3:
            return RTCTRL_TRANSFER_DCI_P3;
        case V4L2_XFER_FUNC_SMPTE2084:
            return RTCTRL_TRANSFER_SMPTE2084;
        default:
            return RTCTRL_TRANSFER_UNKNOWN;
    }
}

static uint32_t map_ycbcr(uint32_t value) {
    switch (value) {
        case V4L2_YCBCR_ENC_709:
            return RTCTRL_YCBCR_BT709;
        case V4L2_YCBCR_ENC_601:
            return RTCTRL_YCBCR_BT601;
        case V4L2_YCBCR_ENC_SYCC:
            return RTCTRL_YCBCR_BT601;
        case V4L2_YCBCR_ENC_BT2020:
            return RTCTRL_YCBCR_BT2020;
        case V4L2_YCBCR_ENC_BT2020_CONST_LUM:
            return RTCTRL_YCBCR_BT2020_CONSTANT;
        case V4L2_YCBCR_ENC_SMPTE240M:
            return RTCTRL_YCBCR_SMPTE240M;
        case V4L2_YCBCR_ENC_XV601:
            return RTCTRL_YCBCR_XV601;
        case V4L2_YCBCR_ENC_XV709:
            return RTCTRL_YCBCR_XV709;
        default:
            return RTCTRL_YCBCR_UNKNOWN;
    }
}

void rtctrl_v4l2_translate_format(const struct v4l2_pix_format_mplane* f,
                                  struct rtctrl_camera_format* output) {
    struct rtctrl_camera_format converted = {0};
    converted.width = f->width;
    converted.height = f->height;
    converted.plane_count = f->num_planes;
    converted.native_format = f->pixelformat;
    converted.pixel_format = map_pixel(f->pixelformat);
    converted.colorspace = map_color(f->colorspace);
    uint32_t xfer = f->xfer_func, ycbcr = f->ycbcr_enc, range = f->quantization;
    /* Only resolve defaults when the declared metadata supplies enough context.
     * Never infer colorspace from image dimensions or an unknown vendor value. */
    if (converted.colorspace != RTCTRL_COLOR_UNKNOWN) {
        if (xfer == V4L2_XFER_FUNC_DEFAULT) {
            xfer = V4L2_MAP_XFER_FUNC_DEFAULT(f->colorspace);
        }
        if (ycbcr == V4L2_YCBCR_ENC_DEFAULT) {
            ycbcr = V4L2_MAP_YCBCR_ENC_DEFAULT(f->colorspace);
        }
    }
    const int rgb = converted.pixel_format == RTCTRL_PIXEL_RGB24 ||
                    converted.pixel_format == RTCTRL_PIXEL_BGR24;
    const int yuv = converted.pixel_format >= RTCTRL_PIXEL_NV12 &&
                    converted.pixel_format <= RTCTRL_PIXEL_UYVY;
    if (range == V4L2_QUANTIZATION_DEFAULT &&
        (rgb || (yuv && converted.colorspace != RTCTRL_COLOR_UNKNOWN))) {
        range = V4L2_MAP_QUANTIZATION_DEFAULT(rgb, f->colorspace, ycbcr);
    }
    converted.transfer_function = map_xfer(xfer);
    converted.ycbcr_encoding = yuv ? map_ycbcr(ycbcr) : RTCTRL_YCBCR_UNKNOWN;
    converted.quantization = map_range(range);
    *output = converted;
}
