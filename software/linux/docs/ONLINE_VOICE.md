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

未配置语音唤醒时，应用启动后处于离线、麦克风静音状态。在网页中连接语音服务、取消静音并开始说话。使用 `realtime` 模式时，说完后自然停顿，等待服务端回复。板端录放音配置见[音频接入](../deploy/patchx/AUDIO.md)。

## 语音唤醒和连续对话

在启用板端音频的在线配置中，将 `patchx.wake_assets` 设置为本地模型目录的绝对路径，
并将 `patchx.wake_aec_library` 设置为可加载的 ARM64 回声消除库的绝对路径。
运行环境须包含与 Python 版本和架构匹配的 `sherpa-onnx` 及其依赖。

在 ARM64 Linux 上安装 [SpeexDSP](https://www.speex.org/downloads/) 开发包后，
可以从仓库根目录构建回声消除库：

```sh
cc -O2 -fPIC -shared software/linux/tools/audio/wake-aec.c \
  $(pkg-config --cflags --libs speexdsp) -o libpatchx_wake_aec.so
```

将库和运行时依赖放进板端运行目录，再把 `wake_aec_library` 指向该库。
设置 `voice.aec_enable_aes=true` 可抑制剩余回声。

模型目录放入 sherpa-onnx 中文关键词模型的 `encoder.onnx`、`decoder.onnx`、
`joiner.onnx`、`tokens.txt`，以及 `silero_vad.onnx` 和 `keywords.txt`。
使用 [WenetSpeech 中文关键词模型](https://k2-fsa.github.io/sherpa/onnx/kws/pretrained_models/index.html)
时，在 `keywords.txt` 中写入一行：

```text
p à q í p à q í @帕奇帕奇
```

如需唤醒应答，把 `patchx.wake_prompt_wav` 指向运行环境内的本地 WAV 文件，
例如手头 Android 工程里的 `zai.wav`。文件须为单声道 PCM16，最长三秒。
PatchX 在语音服务连接就绪后播放这段提示，播完再开启麦克风上传。

启动 PatchX 后，说「帕奇帕奇」。听到本地提示后可以开口，听完回答可以直接追问。
语音唤醒使用 Bothlent 八通道采集。第 1 路为麦克风，第 7 路为当前单声道播放路由的扬声器参考，
两路一起采样。第 8 路参考与第 7 路可能反相，不能将两者取平均。播报期间保留麦克风采集，播完自动续听。
`patchx.wake_barge_in` 可开启实验性的语音打断。开启时应用会在播报期间检测插话，
同时满足人声判断和能量门后累计五个语音帧才停止当前播报，累计十二个静音帧后清空语音计数。
采集帧长为 60 ms。`patchx.wake_barge_min_rms` 设置回声消除后的 PCM16 RMS 能量下限，
默认 1000，须按麦克风增益、距离和播放音量调整。可在状态接口的
`patchx.barge_in` 中查看 RMS 和计数，接口只提供数值统计。
部署时默认关闭此选项。
待机语音由本地检测器在内存中处理，唤醒后才向在线服务上传。网页的静音和断开
按钮会关闭唤醒监听，取消静音可恢复待机。
