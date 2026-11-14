# RK3568 音频实机验证（2026-11-14）

两台 3568A 均运行原厂 Android 11 / Linux 4.19.232。播放使用 `tinyplay` 直连 RK809，格式为 48 kHz、S16_LE、双声道。听感由现场确认。

| 板卡 | 播放条件 | 结果 |
| --- | --- | --- |
| 旧板 | `HP_NO_MIC`，双声道同时播放「乙组，火车飞机」 | 两遍均清楚 |
| 旧板 | `HP_NO_MIC`，左、右分开播放，另一声道的样本全零 | 两轮均清楚，未核对实际发声的喇叭位置 |
| 新板 | `SPK` 播「甲组，苹果香蕉」，`HP_NO_MIC` 播「乙组，火车飞机」 | 现场只听清乙组。原厂 PatchX 运行时也观察到 `HP_NO_MIC`。该路由下的左右独立播放待测 |

测试结束后已关闭播放 PCM、恢复 `Playback Path=OFF` 并删除测试样本。

新板 Bothlent 原始输入以 16 kHz、S16_LE 采集八路。安静 5 秒与贴近说话 16 秒对照中，前四路去直流 RMS 由 213.2 / 234.9 / 238.4 / 243.9 升至 1185.0 / 1276.5 / 1339.5 / 1449.8，约为原来的 5.4–5.9 倍，后四路基本持平。旧板接上 H3/H8 后直采八路，现有复测未见明显讲话响应，仍待排查。

## 旧板原生 Linux 播放

旧板从 RAM 启动 Linux 5.10.160-rt89（40 MiB 镜像 CRC32 `1d4f9d74`）。原驱动下 SPK 只有呲声，HP_NO_MIC 无声。新版 RK817 模块在 3568A 的 HP_NO_MIC 播放时启用 GPIO148 外部功放，保留驱动原有的停播静音处理。

通过 PatchX `PatchXAudioIO.play_pcm()` 输入 24 kHz、S16_LE 单声道「乙组，火车飞机」，经发布版 ALSA 配置送入 RK809。PatchX 自行选择路由。现场确认听清。

| 时点 | Playback Path | PCM | GPIO148 |
| --- | --- | --- | --- |
| 播放前 | OFF | closed | 高，功放关闭 |
| 播放中 | HP_NO_MIC | RUNNING | 低，功放使能 |
| 播放后 | OFF | closed | 高，功放关闭 |

播放器无错误，PatchX 释放扬声器所有权，探针退出 0。音频单元与集成测试共 23 项通过。验证范围为旧板的本地 PCM 播放，云端全链路、旧板收音和新板原生 Linux 听感待验收。

[Android 分支](https://github.com/patchx2027/Patchx-OpenDeskRobot/tree/android) · [Linux 分支](https://github.com/patchx2027/Patchx-OpenDeskRobot/tree/linux)
