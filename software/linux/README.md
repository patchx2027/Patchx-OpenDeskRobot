# PatchX Linux 应用

这里提供 PatchX 的 Python 应用、浏览器界面和 RK3568 板级适配源码。Python 命令均从本目录 `software/linux` 执行；内核、设备树和内核构建说明在仓库根目录。

配置流式语音时，按[在线语音配置](docs/ONLINE_VOICE.md)填写自己的服务身份，并按[音频接入](deploy/patchx/AUDIO.md)配置麦克风和扬声器。

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
| PatchX App 手动启动 | [手动启动说明](docs/GETTING_STARTED.md) · [Python 入口](apps/patchx/__main__.py) |
| PatchX App 自动启动 | [部署与随 RAM Linux 自启动](../../Documentation/patchx/RAM_APP.md) · [自启动代码](deploy/patchx/ram/init) |

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