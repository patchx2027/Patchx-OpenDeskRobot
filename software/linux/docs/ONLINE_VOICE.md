# 在线语音配置

PatchX 通过 WebSocket 上传 Opus 并播放服务端回复。

## 配置连接

[在线模板](../deploy/patchx/live.example.json)提供以下连接配置示例。

```text
ws://118.31.102.180:8360/?device_id=<应用SN>&user_id=<应用SN>
```

- URL 中的两个参数都使用与 `voice.device_id` 相同的应用 SN，并对参数值做 URL 编码。程序不会自动替换模板占位符。
- `voice.client_id` 使用对应服务要求的客户端标识，Bearer 令牌从 `voice.token_env` 指定的环境变量读取。
- `voice.listen_mode="realtime"`，`voice.websocket_heartbeat=false`。
- 输入为 16 kHz 单声道裸 Opus，每包 60 ms / 960 个样本。输出采样率由服务端 hello 协商，输入采样率保持 16 kHz。

该地址是明文 `ws://`，模板显式设置 `voice.allow_insecure_ws=true`，连接中的身份、令牌和音频没有 TLS 保护。请仅使用自己获授权的服务及身份。配置步骤见 [开始使用](GETTING_STARTED.md#配置在线服务)。

## 开始对话

应用默认离线且麦克风静音。在网页中连接语音服务、取消静音并开始说话。使用 `realtime` 模式时，说完后自然停顿，等待服务端回复。板端录放音配置见[音频接入](../deploy/patchx/AUDIO.md)。
