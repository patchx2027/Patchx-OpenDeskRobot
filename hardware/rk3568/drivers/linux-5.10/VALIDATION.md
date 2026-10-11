# 已验证功能

本页记录驱动对应的有限实机结果。源码身份见 [manifest.json](manifest.json)，按功能组织的结果见 [features.json](features.json)。音频、USB 输入和显示记录于 2026-10-11；Wi-Fi 与 RNG 记录于 2026-10-04，各项按下列设备与配置条件验证。

## RK809 扬声器播放

板载扬声器通过 SPK 路由播放 1 kHz 短音，现场确认可听见。测试使用 48 kHz、`S16_LE`、双通道，音量样本峰值为 `1024/32768`；结束后关闭播放流，相关路由恢复 OFF。

此项公开范围是板载扬声器的有限播放功能。

## PCM 资源生命周期

RK809 声卡完成 10 个有限阶段：

| 阶段 | 覆盖内容 |
| --- | --- |
| 4 种打开/关闭顺序 | playback/capture 的 peer 组合，保持未 START |
| 2 次单向传输 | playback、capture 各传输 24576 帧，完成 DROP/HW_FREE/CLOSE |
| 4 种双向启停顺序 | 同时进展、单路 DROP 后另一方向继续进展、重启及单路关闭后的另一方向进展 |

各阶段程序和前后资源检查均成功。该项验证 PCM 数据通路与资源生命周期，测试参数为 48 kHz、`S16_LE`、双通道。

## USB 音频采集

使用固定 SDK 中的 USB Audio 驱动，通过 16 kHz、`S16_LE`、8 通道接口进行安静与讲话对照，每个窗口每通道 32000 帧。

前 4 个逻辑通道（0–3）具有明确讲话响应，去均值 AC RMS 比安静窗口提高约 3.323–3.787 倍。采集结束后完成 DROP/HW_FREE/CLOSE，USB 恢复 closed/Stop。这里的 8 通道是接口格式，已验证功能是前 4 个逻辑通道的讲话响应。

## 720×720 显示

VOP2/DSI0 驱动面板，在 `720×720`、`32 bpp`、stride `2880` 的布局下显示五秒彩条，现场确认画面可见。结束后恢复原像素，背光恢复为 0。

## Wi-Fi 双向传输

BCMDHD 使用匹配的板卡 firmware/NVRAM，在默认配置文件 `/lib/firmware/config.txt` 中设置：

```text
PM=0
band=a
```

上述配置在实测中使用 5 GHz、5220 MHz，完成认证、DHCP 及电脑与板端之间的 64 KiB TCP 上传和回传。上传文件、板端文件、回传文件的 SHA-256 一致：

```text
7daca2095d0438260fa849183dfc67faa459fdf4936e1bc91eec6b281b27e4c2
```

该项对应 `PM=0` 与 `band=a` 配置下的有限传输窗口；复现时使用驱动的 `band=a` 配置语法和匹配的固件输入。

## 硬件随机数接口

设备 `fe388000.rng` 绑定 `rockchip-rng`，`rng_current=rockchip`。内核报告 CRNG 就绪，网络认证前后调用非阻塞 `getrandom(1, GRND_NONBLOCK)` 均成功。

该项验证驱动绑定与随机数接口就绪，结果范围为接口功能。
