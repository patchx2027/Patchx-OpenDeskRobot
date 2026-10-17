#include "rk_aiq_user_api2_sysctl.h"
#include <cerrno>
#include <cstdio>
#include <cstring>

extern "C" int camera_capture_main(int argc, char** argv);

namespace {
constexpr int kMaxSensors = 8;
constexpr unsigned kSensorWidth = 2592;
constexpr unsigned kSensorHeight = 1944;
constexpr int kIsp21PublicCode = 5;

// V3 enumStaticMetas exposes the rkisp_vN driver suffix. The SDK constructor
// maps 4/5/6 to its internal algorithm versions 20/21/30; these are distinct.
constexpr int sdk_version_from_public_code(int code) {
    return code == 4 ? 20 : code == 5 ? 21 : code == 6 ? 30 : -1;
}

struct Receipt {
    const char* first_error = "none";
    int first_rc = 0;
    int first_errno = 0;
    int stop_rc = 0;
    int stop_errno = 0;
    int deinit_errno = 0;
    int public_isp_code = -1;
    int mapped_sdk_isp_version = -1;
    bool deinit_returned = false;
    bool cleanup_uncertain = false;
};

void phase(const char* name, const char* action, int rc = 0, int error = 0) {
    std::fprintf(stderr,
                 "{\"aiq_finite\":1,\"phase\":\"%s\",\"action\":\"%s\",\"rc\":%d,"
                 "\"errno\":%d}\n",
                 name,
                 action,
                 rc,
                 error);
    std::fflush(stderr);
}

void fail(Receipt& receipt, const char* name, int rc, int error) {
    if (std::strcmp(receipt.first_error, "none") == 0) {
        receipt.first_error = name;
        receipt.first_rc = rc;
        receipt.first_errno = error;
    }
}

void final_receipt(const Receipt& receipt) {
    std::fprintf(
        stderr,
        "{\"aiq_finite\":1,\"phase\":\"final\",\"first_error\":\"%s\","
        "\"first_rc\":%d,\"first_errno\":%d,\"stop_rc\":%d,\"stop_errno\":%d,"
        "\"deinit_returned\":%s,\"deinit_errno\":%d,\"cleanup_uncertain\":%s,"
        "\"public_isp_code\":%d,\"mapped_sdk_isp_version\":%d,"
        "\"physical_cleanup_verified\":false}\n",
        receipt.first_error,
        receipt.first_rc,
        receipt.first_errno,
        receipt.stop_rc,
        receipt.stop_errno,
        receipt.deinit_returned ? "true" : "false",
        receipt.deinit_errno,
        receipt.cleanup_uncertain ? "true" : "false",
        receipt.public_isp_code,
        receipt.mapped_sdk_isp_version);
    std::fflush(stderr);
}

void sensor_receipt(const char* name,
                    int public_isp_code,
                    int mapped_sdk_isp_version) {
    std::fprintf(stderr, "{\"aiq_finite\":1,\"phase\":\"sensor\",\"name\":\"");
    for (const auto* byte = reinterpret_cast<const unsigned char*>(name); *byte;
         ++byte) {
        if (*byte < 0x20 || *byte >= 0x7f || *byte == '"' || *byte == '\\') {
            std::fprintf(stderr, "\\u%04x", static_cast<unsigned>(*byte));
        } else {
            std::fputc(*byte, stderr);
        }
    }
    std::fprintf(
        stderr,
        "\",\"isp_hw_ver\":%d,\"public_isp_code\":%d,"
        "\"mapped_sdk_isp_version\":%d,\"sensor_width\":%u,\"sensor_height\":%u,"
        "\"main_width\":640,\"main_height\":480,\"warmup_frames\":30}\n",
        public_isp_code,
        public_isp_code,
        mapped_sdk_isp_version,
        kSensorWidth,
        kSensorHeight);
    std::fflush(stderr);
}

int select_sensor(char* name, std::size_t capacity, int& isp_hw_ver) {
    unsigned matches = 0;
    bool enumeration_ended = false;
    for (int index = 0; index <= kMaxSensors; ++index) {
        rk_aiq_static_info_t info{};
        errno = 0;
        phase("enum", "enter");
        const auto rc = rk_aiq_uapi2_sysctl_enumStaticMetas(index, &info);
        const int error = errno;
        phase("enum", "return", rc, error);
        if (rc != XCAM_RETURN_NO_ERROR) {
            enumeration_ended = true;
            break;
        }
        if (index == kMaxSensors ||
            !std::memchr(info.sensor_info.sensor_name,
                         '\0',
                         sizeof(info.sensor_info.sensor_name))) {
            return EOVERFLOW;
        }
        const char* sensor = info.sensor_info.sensor_name;
        if (std::strstr(sensor, "_ov5695 ")) {
            if (std::strlen(sensor) >= capacity) {
                return ENAMETOOLONG;
            }
            ++matches;
            std::strcpy(name, sensor);
            isp_hw_ver = info.isp_hw_ver;
        }
    }
    if (!enumeration_ended || matches != 1) {
        return matches ? EEXIST : ENODEV;
    }
    return 0;
}
} // namespace

int main(int argc, char** argv) {
    if (argc != 5 || std::strcmp(argv[1], "--capture-once") != 0 ||
        argv[2][0] != '/' || argv[3][0] != '/' || argv[4][0] != '/' ||
        std::strcmp(argv[3], argv[4]) == 0) {
        return 2;
    }
    Receipt receipt;
    char sensor[32]{};
    int isp_hw_ver = 0;
    const int sensor_error = select_sensor(sensor, sizeof(sensor), isp_hw_ver);
    if (sensor_error) {
        fail(receipt, "sensor-enumeration", -1, sensor_error);
        final_receipt(receipt);
        return 1;
    }
    receipt.public_isp_code = isp_hw_ver;
    receipt.mapped_sdk_isp_version = sdk_version_from_public_code(isp_hw_ver);
    sensor_receipt(sensor, receipt.public_isp_code, receipt.mapped_sdk_isp_version);
    if (receipt.public_isp_code != kIsp21PublicCode) {
        fail(receipt, "sensor-hardware", -1, ENOTSUP);
        final_receipt(receipt);
        return 1;
    }

    errno = 0;
    phase("init", "enter");
    rk_aiq_sys_ctx_t* context =
        rk_aiq_uapi2_sysctl_init(sensor, argv[2], nullptr, nullptr);
    const int init_errno = errno;
    phase("init", "return", context ? 0 : -1, init_errno);
    if (!context) {
        fail(receipt, "init", -1, init_errno);
        final_receipt(receipt);
        return 1;
    }

    errno = 0;
    phase("prepare", "enter");
    const auto prepare_rc = rk_aiq_uapi2_sysctl_prepare(
        context, kSensorWidth, kSensorHeight, RK_AIQ_WORKING_MODE_NORMAL);
    const int prepare_errno = errno;
    phase("prepare", "return", prepare_rc, prepare_errno);
    if (prepare_rc != XCAM_RETURN_NO_ERROR) {
        fail(receipt, "prepare", prepare_rc, prepare_errno);
    } else {
        errno = 0;
        phase("start", "enter");
        const auto start_rc = rk_aiq_uapi2_sysctl_start(context);
        const int start_errno = errno;
        phase("start", "return", start_rc, start_errno);
        if (start_rc != XCAM_RETURN_NO_ERROR) {
            fail(receipt, "start", start_rc, start_errno);
        } else {
            char mode[] = "--capture-once-640x480-nv12";
            char* capture_argv[] = {argv[0], mode, argv[3], argv[4], nullptr};
            errno = 0;
            phase("capture", "enter");
            const int capture_rc = camera_capture_main(4, capture_argv);
            const int capture_errno = errno;
            phase("capture", "return", capture_rc, capture_errno);
            if (capture_rc != 0) {
                // A failed capture may include failed STREAMOFF/munmap/close.
                // Its separate receipt supplies the precise cleanup results.
                receipt.cleanup_uncertain = true;
                fail(receipt, "capture", capture_rc, capture_errno);
            }
        }
    }

    // prepare/start can return after partial resource acquisition. Stop before
    // deinit even on these failures; deinit has no public return status.
    errno = 0;
    phase("stop", "enter");
    receipt.stop_rc = rk_aiq_uapi2_sysctl_stop(context, false);
    receipt.stop_errno = errno;
    phase("stop", "return", receipt.stop_rc, receipt.stop_errno);
    if (receipt.stop_rc != XCAM_RETURN_NO_ERROR) {
        receipt.cleanup_uncertain = true;
        fail(receipt, "stop", receipt.stop_rc, receipt.stop_errno);
    }
    phase("deinit", "enter");
    errno = 0;
    rk_aiq_uapi2_sysctl_deinit(context);
    receipt.deinit_errno = errno;
    receipt.deinit_returned = true;
    phase("deinit", "return", 0, receipt.deinit_errno);
    final_receipt(receipt);
    return std::strcmp(receipt.first_error, "none") == 0 ? 0 : 1;
}
