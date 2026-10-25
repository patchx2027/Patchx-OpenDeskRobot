# PatchX Linux 应用

这里提供 PatchX 的 Python 应用、浏览器界面和 RK3568 板级适配源码。Python 命令均从本目录 `software/linux` 执行；内核、设备树和内核构建说明在仓库根目录。

**在线语音已有主机验证通过的配置。** 2026 年 10 月 25 日，`ws://118.31.102.180:8360/` 的文字与合成语音输入各连续三轮获得正确回答和可解码音频。模板使用相同地址和 `realtime` 模式；身份及令牌需本地填写，板端真实收音和播放尚未验收。详见 [在线语音状态](docs/ONLINE_VOICE.md)。

## 从哪里开始

| 想做什么 | 入口 |
| --- | --- |
| 安装依赖，先运行不接硬件的演示 | [开始使用](docs/GETTING_STARTED.md) |
| 配置自己的在线服务和设备身份 | [在线配置步骤](docs/GETTING_STARTED.md#配置在线服务) |
| 连接 USB 麦克风与 RK809 扬声器 | [板端音频](deploy/patchx/AUDIO.md) |
| 编译相机 helper 和准备外部依赖 | [相机说明](docs/CAMERA.md) |
| 阅读业务消息和硬件开关 | [PatchX 应用](apps/patchx/README.md) |
| 阅读语音状态机、WebSocket 和 HTTP 服务 | [Companion 公共模块](apps/companion/README.md) |
| 构建内核与设备树 | [内核构建说明](../../Documentation/patchx/BUILD.md) |

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