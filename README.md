# PatchX Linux

PatchX RK3568 桌面机器人的 Linux 源码。本分支提供完整内核、板级设备树、Python 用户程序，以及相机、JPEG 和进程管理辅助工具。

Android 应用源码在 [`android` 分支](https://github.com/patchx2027/Patchx-OpenDeskRobot/tree/android)。

## 在线语音状态

旧版 3568A 已在原生 Linux 上使用 `8360` 服务完成一次实麦流式对话。现场自然说完并停顿后听清了正确回复「12」。测试未保存录音。

在线示例使用 `ws://118.31.102.180:8360/` 和 `realtime` 模式。应用身份、客户端标识和令牌由使用者在本地填写。配置步骤和实测范围见[在线语音说明](software/linux/docs/ONLINE_VOICE.md)。

GD32 串口帧与流式语音的共同约定及测试样例见 [共享协议 v1](https://github.com/patchx2027/Patchx-OpenDeskRobot/tree/main/protocol/v1)。

## 从哪里开始

| 想做什么 | 入口 |
| --- | --- |
| 运行演示，修改语音、表情或电机逻辑 | [PatchX 用户程序](software/linux/README.md) |
| 修改驱动或设备树，构建 Image、模块和 DTB | [内核与设备树指南](Documentation/patchx/SOURCE_GUIDE.md) · [内核构建](Documentation/patchx/BUILD.md) |
| 手头只有原厂 Android，想联网、了解烧录并启动 Linux | [原厂 Android 准备、联网与启动](Documentation/patchx/ANDROID_START.md) · [Linux RAM 启动步骤](Documentation/patchx/RAM_BOOT.md) |

仓库根目录的 [README](README) 随内核源码保留，面向内核开发者。当前的 [README.md](README.md) 是 PatchX 产品首页，从这里找应用、板级文档和启动流程。

```sh
git clone --branch linux --single-branch \
    https://github.com/patchx2027/Patchx-OpenDeskRobot.git Patchx-Linux
cd Patchx-Linux/software/linux
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r apps/patchx/requirements.txt
python3 -m apps.patchx --config apps/patchx/demo.json
```

演示页面为 `http://127.0.0.1:8090/`。演示模式不连接语音服务，也不打开板卡设备。

## 顶层目录

| 目录 | 职责 |
| --- | --- |
| `arch/` | CPU 架构代码。RK3568 设备树在 `arm64/boot/dts/rockchip/`，产品配置在 `arm64/configs/` |
| `block/` | 块设备 I/O 层 |
| `certs/` | 内核证书和密钥管理 |
| `crypto/` | 内核密码算法与接口 |
| `Documentation/` | 内核文档。[`Documentation/patchx/`](Documentation/patchx/) 管理板级启动、构建和实测 |
| `drivers/` | 内核设备驱动，包括 GPIO、显示、USB 和媒体 |
| `fs/` | 文件系统和虚拟文件系统层 |
| `include/` | 内核及用户空间接口头文件 |
| `init/` | 内核启动和初始化代码 |
| `io_uring/` | io_uring 异步 I/O 接口 |
| `ipc/` | 进程间通信 |
| `kernel/` | 调度、时间、中断等内核核心机制 |
| `lib/` | 内核内部通用库 |
| `LICENSES/` | 内核许可证文本 |
| `mm/` | 内存管理 |
| `net/` | 网络协议栈 |
| `samples/` | 内核接口用法示例 |
| `scripts/` | 内核配置、生成、编译和检查脚本 |
| `security/` | 内核安全框架和访问控制 |
| [`software/linux/`](software/linux/README.md) | PatchX 用户程序、配置、文档、原生辅助工具和应用测试 |
| `sound/` | ALSA、ASoC 和 RK817 音频驱动 |
| `tools/` | 内核及主机工具。[`tools/patchx/`](tools/patchx/) 包含旧板 RAM 打包器 |
| `usr/` | 内核构建时的 initramfs 生成和打包支持 |
| `virt/` | 内核虚拟化支持 |

PatchX 应用入口在 `software/linux/apps/patchx/`，通用语音、传输和网页服务在 `software/linux/apps/companion/`。部署配置、应用文档、原生工具和回归测试分别在 `software/linux/` 下的 `deploy/`、`docs/`、`tools/` 和 `tests/`。

## 外设与接入

以下是本分支的 RK3568 产品配置及原生 Linux 支持范围。最小 RAM 启动环境提供串口和驱动检查，语音、表情和相机等应用功能还需部署 PatchX 用户程序。

| 外设 | 在 Linux 上能做什么 | 接入说明 |
| --- | --- | --- |
| Bothlent USB 麦阵、H8 接线 | ALSA 可采集 16 kHz、8 通道 PCM。前 4 路实测有拾音响应，PatchX 基础语音选用逻辑通道 0 | [音频配置](software/linux/deploy/patchx/AUDIO.md) · [H8 接线与采集对照](Documentation/patchx/RK3568-AUDIO-VERIFICATION.md) |
| RK809 与扬声器 | 旧板通过 `HP_NO_MIC` 播放。GPIO148 控制外部功放，播放结束后路由回到 `OFF` | [音频配置](software/linux/deploy/patchx/AUDIO.md) |
| 720×720 DSI 屏幕与背光 | VOP2/DSI0 输出画面。PatchX 可绘制二维表情，并通过配置的背光设备调节亮度 | [显示与背光配置](software/linux/apps/patchx/README.md) |
| OV5695 摄像头 | 经 CSI-2 和 RKISP 采集，配套用户态工具可生成 JPEG。需要匹配的 RKAIQ 库和模组 IQ 参数 | [相机接入](software/linux/docs/CAMERA.md) |
| GD32F503 电机板 | UART 可查询六轴状态、PD 参数和故障。应用提供动作和 OTA 入口，有动力动作和原生 Linux OTA 的整链路仍待验收 | [电机接入](software/linux/apps/patchx/README.md#gd32f503-电机控制) · [串口联调范围](software/linux/docs/MOTOR_NATIVE_LINUX.md) |
| Wi-Fi | BCMDHD 使用匹配的 firmware/NVRAM。原生 Linux 已完成 5 GHz 入网、DHCP 和局域网双向传输 | [设备树与固件说明](Documentation/patchx/SOURCE_GUIDE.md) |
| 调试串口 | `ttyFIQ0` 用于读取启动日志、进入 U-Boot 和 Linux shell | [串口与联网](Documentation/patchx/ANDROID_START.md) · [RAM 启动](Documentation/patchx/RAM_BOOT.md) |

在线语音的流式对话范围和配置见[在线语音说明](software/linux/docs/ONLINE_VOICE.md)。Linux 基础语音路径暂未实现原厂 Android 的 CAE、阵列 AEC 和离线唤醒。

## 来源与许可

内核基线为 [Orange Pi Linux BSP](https://github.com/orangepi-xunlong/linux-orangepi.git) 的 `9f9e9d18574d0914c0d192a90c3babfe1fd63c95`，版本 `5.10.160-rt89`。源码来源和板级配置见 [SOURCE.json](Documentation/patchx/SOURCE.json)，本次用户程序的文件清单见 [RELEASE.json](software/linux/RELEASE.json)。

内核保留原有版权与许可，参见 [COPYING](COPYING) 和 [LICENSES/](LICENSES/)。`software/linux` 中第一方程序采用其 [MIT 许可证](software/linux/LICENSE)；第三方库、算法档案、固件和模组参数遵循各自许可，详见[依赖说明](software/linux/docs/THIRD_PARTY.md)。
