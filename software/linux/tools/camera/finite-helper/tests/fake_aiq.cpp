#include "rk_aiq_user_api2_sysctl.h"
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#ifdef AIQ_SHARED_IO_PROBE
#include <fcntl.h>
#include <unistd.h>
#endif

struct rk_aiq_sys_ctx_s {
    int marker;
};
static rk_aiq_sys_ctx_t context{1};
static bool scenario(const char* name) {
    const char* value = std::getenv("AIQ_FIXTURE");
    return value && std::strcmp(value, name) == 0;
}
static void event(const char* name, unsigned width = 0, unsigned height = 0) {
    std::fprintf(stderr,
                 "FAKE:{\"event\":\"%s\",\"width\":%u,\"height\":%u}\n",
                 name,
                 width,
                 height);
}
extern "C" XCamReturn
rk_aiq_uapi2_sysctl_enumStaticMetas(int index, rk_aiq_static_info_t* info) {
    event("enum");
    if (scenario("no-sensor") || index > (scenario("ambiguous") ? 1 : 0)) {
        return XCAM_RETURN_ERROR_OUTOFRANGE;
    }
    std::memset(info, 0, sizeof(*info));
    if (scenario("unterminated-name")) {
        std::memset(info->sensor_info.sensor_name,
                    'x',
                    sizeof(info->sensor_info.sensor_name));
    } else {
        std::strcpy(info->sensor_info.sensor_name,
                    scenario("wrong-sensor") ? "m00_b_other 2-0036"
                                             : "m00_b_ov5695 2-0036");
    }
    info->isp_hw_ver = scenario("wrong-isp")     ? 20
                       : scenario("isp-code-21") ? 21
                       : scenario("isp-code-4")  ? 4
                       : scenario("isp-code-6")  ? 6
                                                 : 5;
    return XCAM_RETURN_NO_ERROR;
}
extern "C" rk_aiq_sys_ctx_t* rk_aiq_uapi2_sysctl_init(const char* sensor,
                                                      const char* iq,
                                                      rk_aiq_error_cb,
                                                      rk_aiq_metas_cb) {
    event("init");
#ifdef AIQ_SHARED_IO_PROBE
    errno = 0;
    const int probe_fd = open("/dev/video-fixture", O_RDONLY | O_CLOEXEC);
    const int probe_errno = errno;
    std::fprintf(
        stderr, "FAKESHARED:{\"open_rc\":%d,\"errno\":%d}\n", probe_fd, probe_errno);
    if (probe_fd >= 0) {
        close(probe_fd);
    }
#endif
    if (std::strcmp(sensor, "m00_b_ov5695 2-0036") ||
        std::strcmp(iq, "/iq-fixture")) {
        std::abort();
    }
    errno = scenario("init-null") ? EINVAL : 0;
    return scenario("init-null") ? nullptr : &context;
}
extern "C" XCamReturn rk_aiq_uapi2_sysctl_prepare(const rk_aiq_sys_ctx_t* ctx,
                                                  uint32_t width,
                                                  uint32_t height,
                                                  rk_aiq_working_mode_t mode) {
    event("prepare", width, height);
    if (ctx != &context || width != 2592 || height != 1944 ||
        mode != RK_AIQ_WORKING_MODE_NORMAL) {
        std::abort();
    }
    errno = scenario("prepare-fail") ? EIO : 0;
    return scenario("prepare-fail") ? XCAM_RETURN_ERROR_FAILED
                                    : XCAM_RETURN_NO_ERROR;
}
extern "C" XCamReturn rk_aiq_uapi2_sysctl_start(const rk_aiq_sys_ctx_t* ctx) {
    event("start");
    if (ctx != &context) {
        std::abort();
    }
    errno = scenario("start-fail") ? EIO : 0;
    return scenario("start-fail") ? XCAM_RETURN_ERROR_FAILED : XCAM_RETURN_NO_ERROR;
}
extern "C" XCamReturn rk_aiq_uapi2_sysctl_stop(const rk_aiq_sys_ctx_t* ctx,
                                               bool keep_hw) {
    event("stop");
    if (ctx != &context || keep_hw) {
        std::abort();
    }
    errno = scenario("stop-fail") ? EIO : 0;
    return scenario("stop-fail") ? XCAM_RETURN_ERROR_FAILED : XCAM_RETURN_NO_ERROR;
}
extern "C" void rk_aiq_uapi2_sysctl_deinit(rk_aiq_sys_ctx_t* ctx) {
    event("deinit");
    if (ctx != &context) {
        std::abort();
    }
    if (scenario("deinit-errno")) {
        errno = EBUSY;
    }
}
#ifndef AIQ_REAL_CAPTURE
extern "C" int camera_capture_main(int argc, char** argv) {
    event("capture");
    if (argc != 4 || std::strcmp(argv[1], "--capture-once-640x480-nv12") ||
        std::strcmp(argv[2], "/dev/video-fixture") ||
        std::strcmp(argv[3], "/tmp/photo-fixture")) {
        std::abort();
    }
    errno = scenario("capture-fail") ? ETIMEDOUT : 0;
    return scenario("capture-fail") ? 1 : 0;
}
#endif
