# PatchX Linux 应用

这里提供 PatchX 的 Python 应用、浏览器界面和 RK3568 板级适配源码。Python 命令均从本目录 `software/linux` 执行；内核、设备树和内核构建说明在仓库根目录。

旧版 3568A 已在原生 Linux 上通过 `8360` 完成一次实麦流式 ASR → LLM → TTS。现场自然说完停顿后听清正确回复，测试未保存录音。 配置与实测范围见 [在线语音说明](docs/ONLINE_VOICE.md)。

## 从哪里开始

| 想做什么 | 入口 |
| --- | --- |
| 安装依赖，先运行不接硬件的演示 | [开始使用](docs/GETTING_STARTED.md) |
| 配置自己的在线服务和设备身份 | [在线配置步骤](docs/GETTING_STARTED.md#配置在线服务) |
| 连接 USB 麦克风与 RK809 扬声器 | [板端音频](deploy/patchx/AUDIO.md) |
| 编译相机 helper 和准备外部依赖 | [相机说明](docs/CAMERA.md) |
| 阅读业务消息和硬件开关 | [PatchX 应用](apps/patchx/README.md) |
| 联调 GD32F503 电机板、查看状态或执行 OTA | [电机板接入](apps/patchx/README.md#gd32f503-电机控制) |
| 阅读语音状态机、WebSocket 和 HTTP 服务 | [Companion 公共模块](apps/companion/README.md) |
| 构建内核与设备树 | [内核构建说明](../../Documentation/patchx/BUILD.md) |
| 让 PatchX 随旧板 RAM Linux 启动 | [应用部署与自启动](../../Documentation/patchx/RAM_APP.md) |

安装依赖后，可先运行：

```sh
python3 -m apps.patchx --config apps/patchx/demo.json
```

打开 <http://127.0.0.1:8090/>。演示不连接语音服务、不打开声卡或硬件端口。实际模式也从离线、麦克风静音开始，由页面操作准备语音。

## 目录

- `apps/patchx/`：产品入口、业务消息、背光、屏幕、音频、相机与 MQTT 适配。
- `apps/companion/`：语音状态机、WebSocket 传输、PCM/Opus、HTTP 服务与网页。
- `deploy/patchx/`：在线配置模板和板级 ALSA 配置。
- `tools/camera/`：相机 helper 源码；RKAIQ 库和对应传感器 IQ 文件需从有权提供的供应方取得。
- `tests/`：随源码提供的回归测试。

本目录是应用源码发布，不是可直接刷入的整机镜像。服务地址、设备身份、凭据及板卡运行环境需由使用者配置。许可按各文件声明及仓库许可说明执行，第三方依赖保留各自许可。