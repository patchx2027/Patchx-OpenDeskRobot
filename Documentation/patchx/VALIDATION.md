# 已验证功能

以下结果来自 PatchX RK3568 板卡的实际运行记录。音频、USB 输入、显示、相机和树内 codec 记录于 2026-10-11；Wi-Fi 与 RNG 记录于 2026-10-04。各项使用对应的设备树、配置和运行环境，不能合并解释为同一套整机系统已经完成验收。

内核基线为 Linux `5.10.160-rt89`。本分支驱动源码采用实际 audio-v5 构建使用的版本，源码入口见 [SOURCE_GUIDE.md](SOURCE_GUIDE.md)。

## RK809 扬声器播放

通过 SPK 路由播放 1 kHz 短音，现场确认可听见。参数为 48 kHz、`S16_LE`、双通道，音量样本峰值 `1024/32768`；结束后关闭播放流，路由恢复 OFF。

## PCM 资源生命周期

RK809 声卡完成 10 个有限阶段：

| 阶段 | 检查内容 |
| --- | --- |
| 4 种打开/关闭顺序 | playback/capture 的 peer 组合，保持未 START |
| 2 次单向传输 | playback、capture 各传输 24576 帧，完成 DROP/HW_FREE/CLOSE |
| 4 种双向启停顺序 | 同时进展、单路 DROP 后另一方向继续进展、重新启动及单路关闭后的另一方向进展 |

各阶段程序和前后资源检查均成功，参数为 48 kHz、`S16_LE`、双通道。这些检查针对 PCM 数据传输和资源的申请、释放。

## USB 音频输入

通过 16 kHz、`S16_LE`、8 通道接口进行安静与讲话对照，每个窗口每通道 32000 帧。前 4 个逻辑通道（0–3）有明确讲话响应，去均值 AC RMS 比安静窗口提高约 3.323–3.787 倍。

结束后完成 DROP/HW_FREE/CLOSE，USB 恢复 closed/Stop。8 通道是接口格式，该项确认的是前 4 个逻辑通道的讲话响应。

## 720×720 显示

VOP2/DSI0 驱动面板，在 `720×720`、`32 bpp`、stride `2880` 的布局下显示五秒彩条，现场确认画面可见。结束后恢复原像素，背光恢复为 0。

## Wi-Fi 双向传输

BCMDHD 使用匹配的板卡 firmware/NVRAM，默认配置文件 `/lib/firmware/config.txt` 内容为：

```text
PM=0
band=a
```

在 5 GHz、5220 MHz 下完成认证、DHCP，以及电脑与板端之间的 64 KiB TCP 上传和回传。上传文件、板端文件与回传文件的 SHA-256 一致：

```text
7daca2095d0438260fa849183dfc67faa459fdf4936e1bc91eec6b281b27e4c2
```

复现这一传输条件时，保留 `PM=0` 和 `band=a`，并使用匹配的固件。

## 硬件随机数接口

`fe388000.rng` 绑定 `rockchip-rng`，`rng_current=rockchip`。内核报告 CRNG 就绪，网络认证前后调用非阻塞 `getrandom(1, GRND_NONBLOCK)` 均成功。检查范围为驱动绑定和接口就绪。

## 树内 RK817 codec 模块

使用原内核树 `CONFIG_SND_SOC_RK817=m` 入口构建模块，实际在原 audio-v5 Image 上加载，插入前后内核 taint 均为 0。声卡初始三路为 OFF，空闲资源检查通过，随后完成产品音频 owner 装配。这次加载记录没有重新确认讲话输入或可听播放；上面的声音和 PCM 结果来自各自较早的运行记录。

产品配置与原完整配置的差异只有 `CONFIG_SND_SOC_RK817` 从未设置改为 `m`。保留该次实际配置作为 `patchx_rk3568_product.config`。原 Image 没有因该模块构建而重编译。

## OV5695 相机采集

原 Linux 5.10 OV5695 驱动配合 `reset-gpios` flags=1，实际识别 `OV005695`，完成 DPHY 匹配和 ISP notifier。有限采集分别取得 2592×1944 NV12 和 640×480 NV12 的非空帧；每次丢弃两张预热帧，保存下一张有效帧，随后 STREAMOFF、解除映射和关闭成功，sensor 与 ISP 回到 runtime suspended。

后续加入配套用户态 ISP 与 JPEG 编码后，两条原相机请求经真实 loopback WebSocket 返回 640×480 JPEG，分别为 27,353 和 27,947 字节，导出校验后查看未见整体发绿，仍存在亮区截断和色边。此结果覆盖有限拍照与回传，不表示 Android 3A 成像等价。

本分支 `rk3568-patchx-product.dts` 生成的 DTB 与上述最终相机 pre-overlay DTB 做全量静态语义对照：983 个节点、4990 个属性、773 个 phandle 及 FDT 元数据全部相同。产品入口保留原显示/音频入口，再加入相机配置；本次发布未重新构建或启动完整产品 Image。来源及散列见 [SOURCE.json](SOURCE.json)。
