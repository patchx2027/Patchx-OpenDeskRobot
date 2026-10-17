#ifndef RTCTRL_VISION_CAPTURE_BACKEND_H
#define RTCTRL_VISION_CAPTURE_BACKEND_H
#include "rtctrl/capture/capture.h"

#ifdef __cplusplus
extern "C" {
#endif
/* Adapter port, versioned for source-level compatibility (not a plugin ABI).
 * All callbacks return 0 or negative errno. open must leave a NULL context or
 * a close-able context on failure; close always destroys it, even on failure.
 * acquire borrows a frame until release/close; release must end the borrow even
 * on error. close must also handle an outstanding borrow. No callback may call
 * back into its camera. One owner thread, no hidden synchronization. */
struct rtctrl_capture_backend {
    uint32_t version; /* must be 1 */
    int (*open)(const void* config, void** context);
    int (*get_format)(const void* context, struct rtctrl_camera_format* format);
    int (*acquire)(void* context, int timeout_ms, struct rtctrl_camera_frame* frame);
    int (*release)(void* context, uint64_t token);
    int (*close)(void* context);
};
#ifdef __cplusplus
}
#endif
#endif
