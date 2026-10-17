# Companion 公共模块

Companion 提供 Python 语音状态机、WebSocket 传输、PCM/Opus 和本地 HTTP 界面。PatchX 在此基础上装配 RK3568 音频、屏幕及业务接口。

本发布面向 PatchX 的运行入口是 `python3 -m apps.patchx`。安装、演示和板端配置见 [开始使用](../../docs/GETTING_STARTED.md)；在线语音的实际结果见 [在线语音状态](../../docs/ONLINE_VOICE.md)。

## 读源码

| 内容 | 文件 |
| --- | --- |
| 语音状态转换、事件与生命周期 | [core.py](core.py) |
| WebSocket 连接、收发和心跳 | [transport.py](transport.py) |
| 配置默认值、合法类型与取值范围 | [config.py](config.py) |
| 录放音、Opus 编解码 | [audio.py](audio.py) |
| HTTP 请求、状态接口与网页服务 | [server.py](server.py) |
| 独立命令行入口 | [__main__.py](__main__.py) |

PatchX 配置将 Companion 字段放在 `voice` 对象中；Companion 独立配置直接使用这些字段。两者配置结构不同，不能直接互换 JSON。

## 语音与页面行为

- 启动时离线且麦克风静音，显式操作后才连接和采集；采集来自板端麦克风。
- 上传格式为 16 kHz、PCM16 单声道转换的 60 ms Opus；回复播放接受 hello 协商的 16 / 24 / 48 kHz。
- 支持最长录音、响应超时、静音和打断。基础路径播放时不录音。
- 协议没有可靠的轮次 ID，打断会发送 abort 并重建连接，以隔离旧回复，可能丢失服务端会话上下文。
- 页面区分等待识别、等待回答、等待音频与接收音频。`tts.start` 不是扬声器已出声的证明。
- 普通状态读取使用 GET `/api/status`；控制操作经 POST `/api/action`。网络与设备设置使用各自 API，实际是否可用由配置和平台适配决定。

服务默认绑定 loopback。板端远程访问使用教程中的 SSH 隧道；不要把本地控制页直接当作公网服务部署。

## 配置要点

`capture_device` 和 `playback_device` 是 ALSA PCM 名称。RK3568 的转换配置见 [板端音频](../../deploy/patchx/AUDIO.md)，不能将原生 8 通道输入直接当成单声道上传。

`token_env` 存环境变量名称，不存令牌值。基础依赖由 [requirements.txt](requirements.txt) 固定，实际音频还依赖系统 libopus、ALSA 和 alsa-utils。

Companion 的 `websocket_heartbeat` 默认 `true`；PatchX 入口按兼容协议默认改为 `false`。两者都保留服务端 ping 回复、实际关闭处理以及录音、回复时限。

源码还包含其他平台的本地语音、设备设置和网络适配接口。对应模型、原生库、硬件与系统服务需要单独配置，不能将这些接口视为本次 RK3568 发布已验证的功能。