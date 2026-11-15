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

## 功能与实测范围

内核功能见[实测说明](Documentation/patchx/VALIDATION.md)，跨系统录放音和 H8 接线对照见[音频实测](Documentation/patchx/RK3568-AUDIO-VERIFICATION.md)。用户程序包含业务消息、二维表情、软件音量、背光、MQTT 和相机请求处理，具体接线及启用方法见[用户程序说明](software/linux/README.md)。

相机成像需要匹配的 RKAIQ 和模组 IQ 参数。当前辅助程序保留实测版本校验；自行重建后需要按[相机说明](software/linux/docs/CAMERA.md)重新适配。语音使用 USB 逻辑通道 0，没有复现原 Android 的 CAE、阵列 AEC 或离线唤醒。GD32F503 串口控制和 OTA 工具已提供，串口默认留空。接线、操作和实测范围见[电机板接入](software/linux/apps/patchx/README.md#gd32f503-电机控制)。

## 来源与许可

内核基线为 [Orange Pi Linux BSP](https://github.com/orangepi-xunlong/linux-orangepi.git) 的 `9f9e9d18574d0914c0d192a90c3babfe1fd63c95`，版本 `5.10.160-rt89`。源码来源和板级配置见 [SOURCE.json](Documentation/patchx/SOURCE.json)，本次用户程序的文件清单见 [RELEASE.json](software/linux/RELEASE.json)。

内核保留原有版权与许可，参见 [COPYING](COPYING) 和 [LICENSES/](LICENSES/)。`software/linux` 中第一方程序采用其 [MIT 许可证](software/linux/LICENSE)；第三方库、算法档案、固件和模组参数遵循各自许可，详见[依赖说明](software/linux/docs/THIRD_PARTY.md)。
