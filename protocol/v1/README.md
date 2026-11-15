# PatchX 共享协议 v1

Android 和原生 Linux 分别实现客户端。这里维护共用的线上格式和 [测试样例](fixtures.json)。不兼容变更使用新的版本目录。下文的 v1 是共用约定版本，GD32 固件报告的 revision 是独立版本号。

## GD32 串口

- 串口为 115200、8N1，无流控。
- 帧为 `FF FF | len:u16BE | cmd:u8 | payload | crc:u16BE | 55 AA`。长度包含命令和负载。
- CRC 使用 CRC-16/MODBUS，初值 `0xffff`，反射多项式 `0xa001`，覆盖长度、命令和负载字段。线上 CRC 高字节在前。
- 支持两种命令系列的实现应按已确认的固件能力选择命令。Android 的旧动作直接把动作号作为单字节命令，旧角度帧使用 `0xfa`。Linux 的 GD32F503 revision 1 协议使用 `0x6b` 查询版本、`0x66` 加序列号和动作 ID 下发动作，并核对结构化应答。样例覆盖两种命令系列的封帧，不能据此推断同一块板支持两组命令。

## 流式语音

- WebSocket 握手带 `Protocol-Version: 1`、`Device-Id` 和 `Client-Id`。需要鉴权时使用 Bearer，服务地址和身份由部署配置提供。
- 客户端发送 `hello`，声明 Opus、16 kHz、单声道、60 ms。服务端 `hello` 返回非空 `session_id`，`listen.start` 使用该值并设 `mode: realtime`。
- 每条 WebSocket 二进制消息承载一个裸 Opus 包。16 kHz 上行每帧为 960 个样本。服务端先发 `tts.start`，再送回复二进制包，最后发 `tts.stop`。
- 自动开始监听和用户触发监听属于客户端策略。Android 播放端固定以 16 kHz 解码，Linux 可协商 16、24 或 48 kHz。共同样例使用 16 kHz。文本探针、取消原因和厂商私有指令在本版共同范围之外。

## 运行兼容性检查

需要 Python 3、JDK、系统 libopus 和 Linux 应用固定的 websocket-client 依赖。传入 Android 与 Linux 两个检出目录。检查使用实际客户端的封帧实现、Android 音频常量及 Linux WebSocket/Opus 实现。Android WebSocket 运行态及电机命令的板端能力需要另做验证。

```sh
python3 protocol/v1/check.py /path/to/android /path/to/linux
```
