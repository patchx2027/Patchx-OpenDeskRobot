// SPDX-License-Identifier: MIT
#ifndef RTCTRL_RK_CAMERA_MODULE_LOCKED_WIRE_H
#define RTCTRL_RK_CAMERA_MODULE_LOCKED_WIRE_H

/* Locked 5.10 camera-module wire ABI only. The vendor rkmodule_inf/PDAF ABI
 * remains unchanged because prebuilt algorithm archives use those types.
 * This adapter is deliberately limited to the verified OV5695 module with
 * no PDAF data. It must not discard a valid new pd_offset or PDAF table.
 */
#include <errno.h>
#include <stddef.h>
#include <string.h>
#include <sys/ioctl.h>
#include <linux/videodev2.h>
#include "rk-camera-module.h"

struct rtctrl_locked_rkmodule_pdaf_inf {
    __u32 flag;
    __u32 gainmap_width;
    __u32 gainmap_height;
    __u32 dccmap_width;
    __u32 dccmap_height;
    __u32 dcc_mode;
    __u32 dcc_dir;
    __u32 pd_offset;
    __u16 gainmap[RKMODULE_PADF_GAINMAP_LEN];
    __u16 dccmap[RKMODULE_PDAF_DCCMAP_LEN];
} __attribute__((packed));

struct rtctrl_locked_rkmodule_inf {
    struct rkmodule_base_inf base;
    struct rkmodule_fac_inf fac;
    struct rkmodule_awb_inf awb;
    struct rkmodule_lsc_inf lsc;
    struct rkmodule_af_inf af;
    struct rtctrl_locked_rkmodule_pdaf_inf pdaf;
    struct rkmodule_otp_module_inf module_inf;
} __attribute__((packed));

#define RTCTRL_LOCKED_GET_MODULE_INFO \
    _IOR('V', BASE_VIDIOC_PRIVATE + 0, struct rtctrl_locked_rkmodule_inf)

#ifdef __cplusplus
#define RTCTRL_WIRE_ASSERT static_assert
#else
#define RTCTRL_WIRE_ASSERT _Static_assert
#endif
RTCTRL_WIRE_ASSERT(sizeof(struct rkmodule_inf) == 5203, "retained vendor ABI");
RTCTRL_WIRE_ASSERT(sizeof(struct rtctrl_locked_rkmodule_inf) == 5207, "locked wire ABI");
RTCTRL_WIRE_ASSERT(sizeof(struct rtctrl_locked_rkmodule_pdaf_inf) == 2592, "wire PDAF ABI");
RTCTRL_WIRE_ASSERT(offsetof(struct rtctrl_locked_rkmodule_inf, pdaf.pd_offset) == 2612,
                   "wire offset field");
RTCTRL_WIRE_ASSERT(offsetof(struct rtctrl_locked_rkmodule_inf, pdaf.gainmap) == 2616,
                   "wire gainmap offset");
RTCTRL_WIRE_ASSERT(offsetof(struct rtctrl_locked_rkmodule_inf, pdaf.dccmap) == 4664,
                   "wire dccmap offset");
RTCTRL_WIRE_ASSERT(offsetof(struct rtctrl_locked_rkmodule_inf, module_inf) == 5176,
                   "wire trailing module offset");
RTCTRL_WIRE_ASSERT(RTCTRL_LOCKED_GET_MODULE_INFO == 0x945756c0UL, "derived wire ioctl");
#undef RTCTRL_WIRE_ASSERT

static inline int rtctrl_locked_module_to_legacy(
    const struct rtctrl_locked_rkmodule_inf* wire, struct rkmodule_inf* legacy)
{
    if (!wire || !legacy)
        return -EINVAL;
    if (!memchr(wire->base.sensor, '\0', RKMODULE_NAME_LEN) ||
        !memchr(wire->base.module, '\0', RKMODULE_NAME_LEN) ||
        !memchr(wire->base.lens, '\0', RKMODULE_NAME_LEN))
        return -EINVAL;
    if (strcmp(wire->base.sensor, "ov5695") ||
        strcmp(wire->base.module, "TongJu") ||
        strcmp(wire->base.lens, "CHT842-MD"))
        return -EOPNOTSUPP;
    const unsigned char* pdaf = (const unsigned char*)&wire->pdaf;
    for (size_t i = 0; i < sizeof(wire->pdaf); ++i) {
        if (pdaf[i] != 0)
            return -EOPNOTSUPP;
    }

    struct rkmodule_inf converted;
    memset(&converted, 0, sizeof(converted));
    converted.base = wire->base;
    converted.fac = wire->fac;
    converted.awb = wire->awb;
    converted.lsc = wire->lsc;
    converted.af = wire->af;
    converted.pdaf.flag = wire->pdaf.flag;
    converted.pdaf.gainmap_width = wire->pdaf.gainmap_width;
    converted.pdaf.gainmap_height = wire->pdaf.gainmap_height;
    converted.pdaf.dccmap_width = wire->pdaf.dccmap_width;
    converted.pdaf.dccmap_height = wire->pdaf.dccmap_height;
    converted.pdaf.dcc_mode = wire->pdaf.dcc_mode;
    converted.pdaf.dcc_dir = wire->pdaf.dcc_dir;
    memcpy(converted.pdaf.gainmap, wire->pdaf.gainmap, sizeof(converted.pdaf.gainmap));
    memcpy(converted.pdaf.dccmap, wire->pdaf.dccmap, sizeof(converted.pdaf.dccmap));
    converted.module_inf = wire->module_inf;
    *legacy = converted;
    return 0;
}
#endif
