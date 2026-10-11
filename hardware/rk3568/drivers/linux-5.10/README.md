# RK3568 Linux 5.10 源码

完整内核源码位于本仓库的 [kernel 分支](https://github.com/patchx2027/Patchx-OpenDeskRobot/tree/kernel)，驱动修改已整合，可以直接浏览、下载和修改。

## 从哪里开始

| 想做什么 | 入口 |
| --- | --- |
| 阅读或修改驱动 | [源码阅读指南](https://github.com/patchx2027/Patchx-OpenDeskRobot/blob/kernel/Documentation/patchx/SOURCE_GUIDE.md) |
| 编译内核、设备树和 codec 模块 | [构建说明](https://github.com/patchx2027/Patchx-OpenDeskRobot/blob/kernel/Documentation/patchx/BUILD.md) |
| 修改 Linux 板级设备树 | [rk3568-patchx.dts](https://github.com/patchx2027/Patchx-OpenDeskRobot/blob/kernel/arch/arm64/boot/dts/rockchip/rk3568-patchx.dts) |
| 核对功能和实测条件 | [已验证功能](https://github.com/patchx2027/Patchx-OpenDeskRobot/blob/kernel/Documentation/patchx/VALIDATION.md) |

```sh
git clone --branch kernel --single-branch \
    https://github.com/patchx2027/Patchx-OpenDeskRobot.git Patchx-Kernel
```

声音代码在 `sound/`，DMA、面板、Wi-Fi 和随机数驱动在 `drivers/`。设备树从 `arch/arm64/boot/dts/rockchip/rk3568-patchx.dts` 进入，按供电、连接、音频和显示分文件阅读。

`android` 分支的 [设备树参考](../../device-tree/README.md) 对应 Android 4.19；`kernel` 分支提供与 Linux 5.10 匹配的板级源码和构建配置。修改前先选对应的分支与教程。

内核源码保留各文件的原版权与许可，见 `kernel` 分支的 [COPYING](https://github.com/patchx2027/Patchx-OpenDeskRobot/blob/kernel/COPYING) 和 [LICENSES](https://github.com/patchx2027/Patchx-OpenDeskRobot/tree/kernel/LICENSES)。
