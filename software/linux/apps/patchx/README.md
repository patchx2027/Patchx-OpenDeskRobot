# PatchX 应用

面向 RK3568 的产品入口，复用 Companion 的语音状态机、WebSocket 和浏览器服务，在同一非实时事件线程处理兼容 Android 1.0.1 的业务消息。

首次使用请从 [安装与配置教程](../../docs/GETTING_STARTED.md) 开始。**在线语音尚未完成完整往返验证**，已观察到的问题与心跳修正见 [在线语音状态](../../docs/ONLINE_VOICE.md)。

从 `software/linux` 运行：

```sh
python3 -m apps.patchx --config apps/patchx/demo.json
```

页面在 <http://127.0.0.1:8090/>。启动时离线、麦克风静音；demo 不打开声卡或硬件。GET `/api/status` 的 `patchx` 字段包含业务状态。

## 按任务读源码

| 要改什么 | 入口 |
| --- | --- |
| 启动、停止和组件装配 | [__main__.py](__main__.py)：`create_runtime()`、`Runtime.start()`、`Runtime.close()` |
| 配置字段和硬件开关 | [config.py](config.py)：`validate_product()` |
| 业务命令和参数处理 | [business.py](business.py) |
| 云端握手扩展 | [transport.py](transport.py)：增加 `client_ip` 和每次握手的 `trace_id` |
| 音频与软件音量 | [audio.py](audio.py)，配合 [板端音频](../../deploy/patchx/AUDIO.md) |
| 背光、二维屏幕、相机 | [devices.py](devices.py)、[display.py](display.py)、[camera.py](camera.py) |
| 语音事件与业务装配 | [product.py](product.py) |

`create_runtime(config, ...)` 返回含 `core`、`business`、`server` 的生命周期对象。嵌入使用可传 `http_address=("127.0.0.1", 0)` 分配临时 loopback 端口。

## 业务接口

- MCP `set_brightness` / `adjust_brightness` 保留原应用的相对亮度算法：0 取最小值、100 取最大值，其余按最大值的百分比取整后叠加并限幅。
- MCP `set_volume` / `adjust_volume` 使用原相对算法；启用板端音频后控制 0–15 的软件播放音量。
- `expression_control`、welcome、goodbye 和数字表情映射可送往二维 framebuffer 渲染器。画面写入及读回完成后才推进 `rendered`；这不是 Unity 运行时。
- `vision_recognition` / `secondary_vision_capture` 可交给相机 helper 拍摄和编码，再发送原格式 JPEG 消息；限制为一个活动请求、一个待处理请求，过期会话结果失效。
- `action_control` 保留请求和参数，自动动作端口不可用，应用不自动打开电机串口。
- `updateUserId` 提供消息构造函数，未接入真实人脸身份来源。
- MQTT 3.1.1 使用 Paho 2.1.0，支持状态、心情发布和亮度、音量、表情、模式命令；连接凭据通过环境变量提供。

业务错误记录在本地状态中；没有增加原协议未定义的 MCP 网络 ACK。JPEG 编码后的文本消息遵守 64 KiB 上限。

## 硬件配置

硬件开关默认关闭，开启前均需 `voice.mode=live`。

| 开关 | 要求 |
| --- | --- |
| `patchx.audio_enabled` | 使用基础音频路径，不开启 AEC；配置 ALSA 输入和输出，初始音量由 `volume_initial` 指定 |
| `patchx.backlight_enabled` | 显式绝对路径 `backlight_path`，包含 `max_brightness`、`brightness`、`actual_brightness` |
| `patchx.display_enabled` | `display_device` 对应 720×720、32 位 framebuffer，默认路径 `/dev/fb0` |
| `patchx.camera_enabled` | 显式 helper 和工作目录绝对路径；外部 RKAIQ、IQ 文件与板卡匹配 |
| `patchx.mqtt_enabled` | 显式 broker 及环境变量凭据；默认 TLS 端口 8883 |

背光修改会读回结果，失败时尝试恢复；无法确认恢复时报告 `uncertain=true`。显示端保存原可见像素，关闭时核对布局后恢复并读回。音频清理失败会保留资源所有权并拒绝重连；此时应先查清设备占用和子进程状态。

基础音频没有复现 Android 的 CAE、阵列 AEC 或离线唤醒。在线配置、声卡选择和启动命令统一见 [开始使用](../../docs/GETTING_STARTED.md)。