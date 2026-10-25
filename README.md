# PatchX Linux

PatchX RK3568 桌面机器人的 Linux 源码。本分支提供完整内核、板级设备树、Python 用户程序，以及相机、JPEG 和进程管理辅助工具。

Android 应用源码在 [`android` 分支](https://github.com/patchx2027/Patchx-OpenDeskRobot/tree/android)。

## 在线语音状态

**2026 年 10 月 25 日，`ws://118.31.102.180:8360/` 在主机 Linux 客户端上完成了有限语音问答。** 文字和合成语音输入各在同一连接中连续三轮得到正确回答、可解码 Opus 音频和 `tts.stop`，严格 hello 校验也通过。

在线配置模板已更新为这套服务和 `realtime` 模式，应用身份、客户端标识和令牌仍需使用者在本地填写。该地址使用明文 `ws://`，仅用于已获授权的服务接入。板端真实麦克风收音、扬声器播放及长期运行尚未随这套配置验收。

旧 `8060` 服务在这次测试中仍没有完整回答。成功配置、历史问题和验证范围见[在线语音说明](software/linux/docs/ONLINE_VOICE.md)。

## 从哪里开始

| 想做什么 | 入口 |
| --- | --- |
| 在电脑上先运行演示 | [用户程序入门](software/linux/docs/GETTING_STARTED.md) |
| 配置在线语音和板上设备 | [用户程序说明](software/linux/README.md)、[在线语音](software/linux/docs/ONLINE_VOICE.md) |
| 修改业务消息、表情、声音或相机处理 | [PatchX 应用](software/linux/apps/patchx/README.md) |
| 修改内核驱动、供电、显示或相机接线 | [内核与设备树阅读指南](Documentation/patchx/SOURCE_GUIDE.md) |
| 编译内核、设备树和 RK817 模块 | [内核构建](Documentation/patchx/BUILD.md) |
| 构建相机、JPEG 和原生辅助工具 | [相机接入](software/linux/docs/CAMERA.md)、[原生工具](software/linux/tools/README.md) |
| 查看依赖及需要自备的厂商输入 | [第三方依赖](software/linux/docs/THIRD_PARTY.md) |

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

## 目录

```text
arch/arm64/boot/dts/rockchip/  RK3568 及 PatchX 板级设备树
arch/arm64/configs/           内核构建配置
drivers/、sound/、include/     完整内核源码
Documentation/patchx/         内核阅读、构建和实测说明
software/linux/apps/patchx/   PatchX 业务与硬件端口
software/linux/apps/companion/ 语音、网络传输和网页服务
software/linux/deploy/patchx/  ALSA 配置和在线配置示例
software/linux/tools/         第一方原生辅助源码与构建入口
software/linux/tests/         应用测试源码
```

## 功能与实测范围

内核的声音、显示、USB 采集、Wi-Fi、随机数和相机结果见[实测说明](Documentation/patchx/VALIDATION.md)。用户程序包含业务消息、二维表情、软件音量、背光、MQTT 和相机请求处理，具体接线及启用方法见[用户程序说明](software/linux/README.md)。

相机成像需要匹配的 RKAIQ 和模组 IQ 参数。当前辅助程序保留实测版本校验；自行重建后需要按[相机说明](software/linux/docs/CAMERA.md)重新适配。语音使用 USB 逻辑通道 0，没有复现原 Android 的 CAE、阵列 AEC 或离线唤醒。自动电机端口仍未启用。

## 来源与许可

内核基线为 [Orange Pi Linux BSP](https://github.com/orangepi-xunlong/linux-orangepi.git) 的 `9f9e9d18574d0914c0d192a90c3babfe1fd63c95`，版本 `5.10.160-rt89`。源码来源和板级配置见 [SOURCE.json](Documentation/patchx/SOURCE.json)，本次用户程序的文件清单见 [RELEASE.json](software/linux/RELEASE.json)。

内核保留原有版权与许可，参见 [COPYING](COPYING) 和 [LICENSES/](LICENSES/)。`software/linux` 中第一方程序采用其 [MIT 许可证](software/linux/LICENSE)；第三方库、算法档案、固件和模组参数遵循各自许可，详见[依赖说明](software/linux/docs/THIRD_PARTY.md)。
