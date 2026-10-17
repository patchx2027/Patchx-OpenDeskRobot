# 相机：从业务请求到 JPEG

相机默认关闭。先阅读 `apps/patchx/camera.py` 的 `CommandCameraBackend`，再看 `apps/patchx/isp_capture.py`。前者管理任务、独立输出目录、时限和会话失效；后者连接本板的 ISP、取帧与 JPEG 工具。

```text
MCP 看图请求
  → apps/patchx/product.py
  → camera.py：有限 worker / 输出目录 / 取消与回收
  → isp_capture.py：平台和输入校验
  → aiq-supervisor：期限与子进程回收
  → aiq-finite：init / prepare / start / capture / stop / deinit
  → camera_diag + capture-source：NV12 帧
  → nv12-to-jpeg：限定 JPEG 大小
  → 当前 WebSocket 会话回传
```

## 看哪里、改什么

| 要调整的内容 | 源码入口 |
| --- | --- |
| 请求排队、取消、会话过期 | `apps/patchx/camera.py`、`product.py` |
| 相机程序、工作目录、超时 | `apps/patchx/config.py` 的 `camera_*` 配置 |
| 平台、输入 SHA、帧格式及清理回执 | `apps/patchx/isp_capture.py` |
| RKAIQ 生命周期 | [aiq_finite.cpp](../tools/camera/finite-helper/aiq_finite.cpp) |
| 格式、暖机和有限取帧 | [camera_diag.c](../tools/camera/finite-helper/camera_diag.c)、其 `capture-source/` |
| 子进程身份、超时及回收 | [supervisor.c](../tools/camera/finite-supervisor/supervisor.c)、同目录 `owned-process-cleanup.inc` |
| NV12→JPEG、质量与大小预算 | [nv12_to_jpeg.c](../tools/jpeg/nv12_to_jpeg.c) |

本板配置是 OV5695 的 2592×1944 原生模式，ISP mainpath 输出 640×480 NV12；丢弃前 30 帧，保存第 31 帧。JPEG 转换使用 full-range BT.601 假设，这不是经测量确认的全链路色彩标定。

## 构建第一方工具

构建方法见 [tools/README.md](../tools/README.md)。`build-native.py` 接受显式交叉编译前缀、sysroot、RKAIQ SDK 与库路径。它仅编译，不运行 SDK、打开设备、下载文件或刷写。

相机需要三个产物：`aiq-supervisor`、`aiq-finite` 和 `nv12-to-jpeg`。外部依赖见 [THIRD_PARTY.md](THIRD_PARTY.md)。它们不包含 Python、根文件系统、传感器驱动或 IQ 参数。新产物没有原 RAM 配方中的 `.aarch64` 后缀，不能直接替换原固定路径；改名也不会解决输入 SHA 和运行库不匹配的问题。

## 必须使用匹配的 Linux ISP21 SDK / ABI

实测 SDK 以 TinkerBoard RKAIQ `v3.0x8.3`、提交 `143e8227562713df6965480cfd0ff9464d44442d` 为起点，再做 Linux 5.10 ISP21 接口适配。**直接链接未经适配的上游库不等于实测版本。** 原先未匹配 AWB 布局的版本曾在 prepare 中越界写入 32 字节并中止。

适配涉及三个边界：

1. **内核 ISP 统计/参数格式。** `include/common/rkisp21-config.h` 的统计缓冲在 `frame_id` 后有 `params_id`；参数的 `others` 部分在 `csm_cfg` 后增加 packed 的两字节 `cgc_cfg`（`yuv_limit`、`ratio_en`）。应与目标内核 UAPI 逐字段比对。
2. **module-info ioctl。** 内核传输结构为 5207 字节，算法 SDK 的既有结构为 5203 字节，不能替换整个厂商类型。第一方 [rk-camera-module-locked-wire.h](../tools/camera/abi/rk-camera-module-locked-wire.h) 定义传输结构、派生 ioctl 和受限转换；只接受指定 OV5695 模组及全零 PDAF 数据。在 SDK `hwi/isp20/CamHwIsp20.cpp` 的 `get_sensor_caps` 中先用 `RTCTRL_LOCKED_GET_MODULE_INFO` 读取传输结构，再调用 `rtctrl_locked_module_to_legacy` 转换到原 `sensor_info->mod_info`。`parse_module_info` 或 `get_sensor_caps` 失败时清理两个临时记录并跳过注册，不能继续写入传感器表。
3. **AWB 算法接口。** 在 `include/algos/awb/rk_aiq_types_awb_stat_v201.h` 原 `#ifdef ISP_HW_V30 //for rk3588` 分支之前，为 Linux ISP21 增加以下定义。它们恢复同代 ARM64 算法档案所用字段，不能用切换 ISP30 来替代。

```c
#if defined(ISP_HW_V21) && !defined(ANDROID_OS)
#define AWB_MULTI_WINDOW_EN
#define AWB_EXCWP_RANGE_STAT_EN
#define AWB_WPNUM2_EN
#endif
```

保持 ISP21 的原有位宽与硬件选择逻辑。R4 还比较了九个完整结构的尺寸/偏移与算法档案 DWARF，并用真实算法输出周围的 canary 检查越界；单独检查三个宏存在不能证明新的库已兼容。

原构建还补了 `include/ipc_server/socket_server.h` 的 `<memory>` include，排除了 `rkaiq_3A_server` 和 `tests/rkisp_demo` 目标、未使用的 ISP30 custom-AWB 示例，并仅在 `ISP_HW_V30` 下编译 `aiq_core/RkAiqCore.cpp` 对应的 ISP30 AWB 统计复制分支。这些调整保持自动 AWB 算法档案不变。

原 SDK 构建选项如下。它们用于**已完成以上适配的源码**，不是将原始上游自动变成 R4 的脚本：

```text
CMAKE_SYSTEM_NAME=Linux
ARCH=aarch64
CMAKE_BUILD_TYPE=Release
ISP_HW_VERSION=-DISP_HW_V21
RKAIQ_ENABLE_CAMGROUP=OFF
RKAIQ_ENABLE_PARSER_V1=OFF
CMAKE_DISABLE_FIND_PACKAGE_librga=TRUE
CMAKE_DISABLE_FIND_PACKAGE_libdrm=TRUE
```

第一方 helper 保留 `-DISP_HW_V21`，SDK include 路径为 `include`、`include/uAPI2`、`include/common`、`include/algos`、`include/xcore`、`include/iq_parser`、`include/iq_parser_v2` 和 SDK 根目录。可执行文件的 open/close/ioctl/mmap/munmap/poll/__poll_chk/write 包装保留；不会重写共享 RKAIQ 的内部 I/O。

## 外部库、IQ 与重新集成

模组 IQ 为 `ov5695_TongJu_CHT842-MD.json`，它与 RKAIQ 算法档案未随仓库再分发。不同模组、不同 schema 或不同版本的 JSON 不能仅改名后代用。

`isp_capture.py` 保留以下原实测约束：

- `KERNEL` 为 `5.10.160-rt89-g9f9e9d18574d-dirty`，内核 taint 必须为 0。
- `LOCKED_INPUTS` 逐项核对 9 个文件的 SHA：helper、supervisor、IQ、加载器及五个运行库。
- `/dev/video0` 必须是字符设备；实际模组路径为 `/sys/bus/i2c/devices/2-0036`。
- `regulator.1` 必须 disabled 且 users 为 0；sensor、`rkisp-vir0` 和 `fdff0000.rkisp` 的 runtime PM 必须 suspended。
- 输出目录必须归当前用户所有、权限 0700 且为空；帧尺寸、时间戳、格式和完整清理回执均要符合契约。

因此，新工具链重建后的不同字节、新的库或新的 kernel release 会被拒绝，这是当前适配器的预期行为。适配新环境时，应从实际构建结果生成输入清单，核对 SDK/内核 ABI、模组身份与节点路径，更新相机启动配置及适配器中的精确清单和平台约束，再完成相应静态与板端验收。不要删除 SHA、release、PM 或清理检查来绕过失败。本次发布没有把新构建产物认定为原实测二进制。

## 应用使用的是 wrapper 接口

`patchx.camera_program` 必须指向可执行的 wrapper。`CommandCameraBackend` 会在临时工作目录中调用：

```text
camera_program --output-dir DIR --max-jpeg-bytes N
```

不能把 `aiq-finite` 直接填入该配置：它接受的是 `--capture-once IQ VIDEO OUTPUT`，也不负责 Python 层的完整校验、JPEG 和业务结果文件。wrapper 应固定 Python、源码目录、ISP 运行根和编码器的绝对路径，再转发这两个参数。

下面是待适配的占位模板。先替换所有 `/absolute/` 路径、完成上一节的运行清单与 ABI 验证，再为脚本设置执行权限并配置 `camera_program`；未完成前保持 `camera_enabled=false`。

```sh
#!/bin/sh
set -eu

export PYTHONPATH=/absolute/software/linux
export PYTHONNOUSERSITE=1

exec /absolute/python3 -B -m apps.patchx.isp_capture \
    --isp-root /absolute/verified-isp-runtime \
    --encoder /absolute/bin/nv12-to-jpeg \
    "$@"
```

不要依赖启动时的当前目录：实际调用 cwd 是每次拍摄的临时目录。`--isp-root` 内部仍要满足 `LOCKED_INPUTS` 的固定相对路径和已验证内容；使用新工具的输出名称并不会自动建立这个运行目录。

## 原 RAM 启动器的范围

`tools/launcher/launch-product.c` 保留固定前缀 `/dev/rtctrl-patchx-app-v1/runtime`、原 kernel release、随包 Python 和 `/opt/patchx/live.json` 路径。它不是任意安装目录下的通用 launcher。

该入口仅透传四个凭据环境键，其中语音键是 `RTCTRL_VOICE_TOKEN`；其 live 配置的 `voice.token_env` 必须使用同一个名称。通用 Python 启动教程可选择其它环境变量名（例如 `PATCHX_VOICE_TOKEN`），但不能未经适配就套用到这个固定 RAM 启动器。MQTT 的三个键为 `PATCHX_MQTT_USERNAME`、`PATCHX_MQTT_PASSWORD`、`PATCHX_MQTT_CLIENT_ID`。

## 已有验证范围

已有有限板端记录覆盖 SDK 生命周期、两次应用拍摄、640×480 JPEG 和本地真实 WebSocket 回传。画面未见之前的整体绿偏，但亮区截断、色边以及与 Android 的 3A/长期画质等效尚未验收。相机通过也不表示在线语音对话已经正常。
