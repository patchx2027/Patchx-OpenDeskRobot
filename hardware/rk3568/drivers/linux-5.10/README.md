# RK3568 Linux 5.10 源码

完整内核、设备树和 Linux 用户程序位于本仓库的 [Linux 分支](https://github.com/patchx2027/Patchx-OpenDeskRobot/tree/linux)。驱动修改已整合到源码，可直接浏览、下载和修改。

## 从哪里开始

| 想做什么 | 入口 |
| --- | --- |
| 阅读或修改驱动 | [源码阅读指南](https://github.com/patchx2027/Patchx-OpenDeskRobot/blob/linux/Documentation/patchx/SOURCE_GUIDE.md) |
| 编译内核、设备树和 codec 模块 | [构建说明](https://github.com/patchx2027/Patchx-OpenDeskRobot/blob/linux/Documentation/patchx/BUILD.md) |
| 修改 Linux 板级设备树 | [rk3568-patchx-product.dts](https://github.com/patchx2027/Patchx-OpenDeskRobot/blob/linux/arch/arm64/boot/dts/rockchip/rk3568-patchx-product.dts) |
| 核对功能和实测条件 | [已验证功能](https://github.com/patchx2027/Patchx-OpenDeskRobot/blob/linux/Documentation/patchx/VALIDATION.md) |

```sh
git clone --branch linux --single-branch \
    https://github.com/patchx2027/Patchx-OpenDeskRobot.git Patchx-Kernel
```

声音代码在 `sound/`，DMA、面板、Wi-Fi 和随机数驱动在 `drivers/`。设备树从 `arch/arm64/boot/dts/rockchip/rk3568-patchx-product.dts` 进入，它包含基础板级设备树及相机配置，再按供电、连接、音频、显示和相机分文件阅读。

`android` 分支的 [设备树参考](../../device-tree/README.md) 对应 Android 4.19；Linux 分支提供与 Linux 5.10 匹配的板级源码和构建配置。修改前先选对应的分支与教程。

内核源码保留各文件的原版权与许可，见 Linux 分支的 [COPYING](https://github.com/patchx2027/Patchx-OpenDeskRobot/blob/linux/COPYING) 和 [LICENSES](https://github.com/patchx2027/Patchx-OpenDeskRobot/tree/linux/LICENSES)。

## 用户程序与在线语音

- [运行演示和配置板端程序](https://github.com/patchx2027/Patchx-OpenDeskRobot/blob/linux/software/linux/docs/GETTING_STARTED.md)
- [相机工具构建与接入](https://github.com/patchx2027/Patchx-OpenDeskRobot/blob/linux/software/linux/docs/CAMERA.md)
- [在线语音问题与配置要求](https://github.com/patchx2027/Patchx-OpenDeskRobot/blob/linux/software/linux/docs/ONLINE_VOICE.md)：10 月 25 日主机文字和合成语音问答已通过，板端真实录放音尚未验收。

旧的 [`kernel` 分支](https://github.com/patchx2027/Patchx-OpenDeskRobot/tree/kernel)保留此前的内核版本。
