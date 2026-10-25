# RK3568 板端音频

PatchX 基础音频使用 USB 阵列采集、RK809 扬声器播放。应用处理 16 kHz 单声道输入，ALSA 配置负责连接硬件原生格式。

需要板端已有匹配驱动、libasound、libopus 和 alsa-utils。Python 安装与启动步骤见 [开始使用](../../docs/GETTING_STARTED.md)。

## 核对声卡和路由

先查看板端设备：

```sh
arecord -l
aplay -l
```

提供的 [alsa-rk3568.conf](alsa-rk3568.conf) 使用以下声卡标识：

| PCM 名称 | 硬件与转换 |
| --- | --- |
| `patchx_capture` | `hw:CARD=Dongle,DEV=0`，原生 S16_LE、16 kHz、8 通道；仅选择逻辑通道 0 输出应用单声道 |
| `patchx_playback` | `hw:CARD=rockchiprk809co,DEV=0`，原生 S16_LE、48 kHz、2 通道；将协商的 16 / 24 / 48 kHz 单声道转换并复制到左右输出 |

声卡 ID、格式和接线必须与实际设备一致。尤其扬声器适配器还核对 `rockchiprk809co` 的控件，不能只改 ALSA 名称就认为另一块声卡已适配。

从 `software/linux` 设置本应用的 ALSA 配置：

```sh
export ALSA_CONFIG_PATH="$PWD/deploy/patchx/alsa-rk3568.conf"
```

该文件包含系统 `/usr/share/alsa/alsa.conf`，不会替换系统默认配置。在本地 live JSON 中设置：

```json
{
  "voice": {
    "capture_device": "patchx_capture",
    "playback_device": "patchx_playback"
  },
  "patchx": {
    "audio_enabled": true,
    "volume_initial": 8
  }
}
```

这是要合并到完整配置中的片段，不能单独作为 live 配置启动；服务地址和身份字段按 [教程](../../docs/GETTING_STARTED.md#配置在线服务) 填写。

## 音量与设备生命周期

`apps.patchx.audio.PatchXAudioIO` 构造时不打开声卡，开始录音或播放时才使用设备。基础路径不包含 Android CAE、离线唤醒或 AEC。

播放音量为 0–15，实际 PCM 幅度乘以 `raw / 15`：0 静音，15 保持原幅度。它不修改麦克风增益，也不代表 RK809 模拟增益或 Android 的分贝档位。

`RK809SpeakerRoute` 核对 `Playback Path`、`Capture MIC Path` 和 `Resume Path` 的枚举内容，要求初始均为 OFF。播放前取得 SPK 路由，PCM 子进程回收后恢复 OFF 并读回。共享声卡被其他程序使用时应先处理占用。

停止会分别尝试结束采集、打断播放并等待工作线程。无法确认子进程或设备已释放时，应用保留资源所有权并拒绝新一轮连接。用户态超时不构成内核阻塞调用的硬期限。

## 已验证范围

有限板端记录确认 USB 8 通道接口的前四个逻辑通道有语音响应，应用选择其中通道 0；RK809 扬声器播放和有限 PCM 生命周期流程也已有验证。通道选择不代表波束形成或八个物理麦克风身份已确认。

2026 年 10 月 25 日已完成主机合成语音输入、识别、正确回答与回复音频解码；尚未用这套服务配置验收板端真实收音和扬声器播放。详见 [在线语音与本地音频结果](../../docs/ONLINE_VOICE.md)。内核端的采集、播放验证范围见 [内核验证说明](../../../../Documentation/patchx/VALIDATION.md)。