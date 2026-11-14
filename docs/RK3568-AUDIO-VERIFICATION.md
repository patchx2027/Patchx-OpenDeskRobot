# RK3568 音频实机验证（2026-11-14）

两台 3568A 均运行原厂 Android 11 / Linux 4.19.232。播放使用 `tinyplay` 直连 RK809，格式为 48 kHz、S16_LE、双声道。听感由现场确认。

| 板卡 | 播放条件 | 结果 |
| --- | --- | --- |
| 旧板 | `HP_NO_MIC`，双声道同时播放「乙组，火车飞机」 | 两遍均清楚 |
| 旧板 | `HP_NO_MIC`，左、右分开播放，另一声道的样本全零 | 两轮均清楚，未核对实际发声的喇叭位置 |
| 新板 | `SPK` 播「甲组，苹果香蕉」，`HP_NO_MIC` 播「乙组，火车飞机」 | 现场只听清乙组。原厂 PatchX 运行时也观察到 `HP_NO_MIC`。该路由下的左右独立播放待测 |

测试结束后已关闭播放 PCM、恢复 `Playback Path=OFF` 并删除测试样本。

新板 Bothlent 原始输入以 16 kHz、S16_LE 采集八路。安静 5 秒与贴近说话 16 秒对照中，前四路去直流 RMS 由 213.2 / 234.9 / 238.4 / 243.9 升至 1185.0 / 1276.5 / 1339.5 / 1449.8，约为原来的 5.4–5.9 倍，后四路基本持平。旧板接上 H3/H8 后直采八路，现有复测未见明显讲话响应，仍待排查。

本次验证覆盖原厂 Android 上的物理录放音路径。公开 Android 源码构建和部署、原生 Linux 播放以及云端语音全链路需分别验收。Linux 5.10 候选驱动在 `SPK` 和 `HP_NO_MIC` 下切换不同的功放控制，候选设备树仅声明 `spk-ctl-gpios`，因此 Linux 默认路由保留 `SPK`，待用实际设备树和板端听感验证。

[Android 分支](https://github.com/patchx2027/Patchx-OpenDeskRobot/tree/android) · [Linux 分支](https://github.com/patchx2027/Patchx-OpenDeskRobot/tree/linux)
