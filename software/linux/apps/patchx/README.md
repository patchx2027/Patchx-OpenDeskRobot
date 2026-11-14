# PatchX 应用

面向 RK3568 的产品入口，复用 Companion 的语音状态机、WebSocket 和浏览器服务，在同一非实时事件线程处理兼容 Android 1.0.1 的业务消息。

首次使用请从 [安装与配置教程](../../docs/GETTING_STARTED.md) 开始。2026 年 10 月 25 日，已验证配置在主机上完成文字和合成语音各三轮问答；板端真实收音、播放尚未验收。配置和历史问题见 [在线语音状态](../../docs/ONLINE_VOICE.md)。

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
- `action_control` 记录云端动作意图。启用串口后，电机控制由本地进程调用。
- `updateUserId` 提供消息构造函数，未接入真实人脸身份来源。
- MQTT 3.1.1 使用 Paho 2.1.0，支持状态、心情发布和亮度、音量、表情、模式命令；连接凭据通过环境变量提供。

业务错误记录在本地状态中；没有增加原协议未定义的 MCP 网络 ACK。JPEG 编码后的文本消息遵守 64 KiB 上限。

## GD32F503 电机控制

本板的原生 Linux 串口已确认是 `/dev/ttyS0`。六轴查询及正式入口的实测结果见 [RK3568 原生 Linux 电机板联调](../../docs/MOTOR_NATIVE_LINUX.md)。


在 live 配置中把 `patchx.motor_port` 设为已核实的串口绝对路径，例如 `/dev/ttyS0`。11 月 12 日的实板 UART0 联调运行在 Android 11，设备节点为 `/dev/ttySMT0`。默认空值表示禁用。连接使用 115200 8N1，要求板端支持新版 `0x6B`（revision 1）。启动只同步序号水位、就绪、故障和轴状态。连接恢复后由调用方重新请求运动。

本地控制代码在 runtime 启动后调用 `runtime.core.motor`。命令返回 Future，`result(timeout=...)` 得到带 `result` 和 `status` 的板端应答，`result=0` 表示板端接受。

| 调用 | 用途 |
| --- | --- |
| `set_pd(axis, kp, kd)`、`get_pd(axis)` | 轴号 0–5。空闲时设置流式 PD，并读取生效参数。Kp/Kd 范围均为 0–1，内置动作使用板端原参数。 |
| `begin(mask)` | 显式进入流式模式。低六位对应六轴，至少选一轴。 |
| `target([a0, a1, a2, a3, a4, a5])` | 在 `begin` 被接受后持续提交角度（单位度）。返回值只表示本地最新值槽接受。Linux 每 20 ms 最多发一组新目标，恒定目标也需要定期重新提交。 |
| `action(action_id)` | 使用固件 `action_ids.h` 中的动作 ID。板端接受后切到内置动作。 |
| `stop()`、`clear_faults()` | 停止优先于待发目标。清除只改变故障状态，`busy` 时需等故障确认后再发新请求。 |

配套 revision 1 固件名义每 10 ms 推进插值并计算 PD，连续 100 ms 未收到新的有效目标时停止输出并锁存流式超时。先在受控条件下确认各轴零位、限位、参数和断联后机械表现，再启用运动。

`/api/status` 的 `patchx.motor` 提供 MCU 故障、独立的 Linux 链路故障、轴采样时间及过期标记。流式期间故障仍实时接收，轴位置保留最近一次查询的旧样本。模式表示最近接受的控制方式，板载动作的完成需结合轴反馈判断。网络端点提供只读电机状态，控制调用留在本地进程。

### 电机板 OTA

先退出占用电机串口的 PatchX 服务，确认电机动力已断开。从 `software/linux` 运行维护命令，串口和 APP 镜像路径按实机填写。

```sh
python3 -m apps.patchx.motor_ota --port /dev/ttyS0 /path/to/PatchX_edu.bin
```

工具检查 APP 向量表、独占串口并等待板端接受停止指令，随后逐包上传。输出分别记录传输回执和重启应答，镜像安装仍需独立回读确认。末包回执丢失时，工具保留该结果并继续确认应用是否重启。

2026 年 11 月 12 日，在电机动力断开的条件下，RK3568 与 GD32F503 完成了六轴状态和 PD 参数读取、停止确认、故障上报及清除。上位机 OTA 使用 Android 11 上的临时 ARM64 Linux 用户空间，配合已修复末包回执的电机板固件，收到 165/165 个回执，末包应答用时 1.513 秒。重启后应用恢复应答，J-Link 回读确认镜像及升级状态。带动力运动、位置反馈和断联后的机械表现仍待验收。

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