# PatchX RK3568 Linux 内核

这是 PatchX RK3568 板卡使用的完整 Linux `5.10.160-rt89` 源码，位于 [Patchx-OpenDeskRobot 的 kernel 分支](https://github.com/patchx2027/Patchx-OpenDeskRobot/tree/kernel)。下载后可以直接阅读和构建，驱动修改已包含在源码中。

## 从哪里开始

| 想做什么 | 入口 |
| --- | --- |
| 编译内核、设备树和 RK817 codec 模块 | [构建说明](Documentation/patchx/BUILD.md) |
| 找到声音、显示、USB 或 Wi-Fi 实现 | [源码阅读指南](Documentation/patchx/SOURCE_GUIDE.md) |
| 核对板上的测试条件与结果 | [已验证功能](Documentation/patchx/VALIDATION.md) |
| 查看板级硬件配置 | [rk3568-patchx.dts](arch/arm64/boot/dts/rockchip/rk3568-patchx.dts) |

```sh
git clone --branch kernel --single-branch \
    https://github.com/patchx2027/Patchx-OpenDeskRobot.git Patchx-Kernel
cd Patchx-Kernel
```

## 已验证功能

| 功能 | 实测范围 |
| --- | --- |
| RK809 扬声器 | 短音播放，现场确认可听见 |
| PCM 资源管理 | 10 个阶段，覆盖打开/关闭、单向传输及双向启停 |
| USB 音频输入 | 16 kHz、`S16_LE`、8 通道接口，前 4 个逻辑通道有明确讲话响应 |
| DSI 显示 | 720×720 彩条显示，结束后恢复原像素和背光 |
| Wi-Fi | `PM=0`、`band=a` 的 5 GHz 配置下，64 KiB 双向 TCP 传输一致 |
| 随机数接口 | Rockchip RNG 绑定、CRNG 就绪与非阻塞 `getrandom` 成功 |

各项结果来自对应的设备树、配置和运行环境，具体条件见[验证记录](Documentation/patchx/VALIDATION.md)。这些功能记录不代表整个源码树已经作为同一套整机系统完成验收。

## 来源与许可

基线来自 [Orange Pi Linux BSP](https://github.com/orangepi-xunlong/linux-orangepi.git) 提交 `9f9e9d18574d0914c0d192a90c3babfe1fd63c95`。本分支导入完整源码快照并保留原版权与许可声明，没有导入完整上游 Git 历史。驱动源码采用实际 audio-v5 构建使用的版本；项目入口、配置和文档随仓库提供。来源版本及文件校验值见 [SOURCE.json](Documentation/patchx/SOURCE.json)，上游原始说明保存在 [UPSTREAM-README.md](Documentation/patchx/UPSTREAM-README.md)。

内核及各文件遵循原有许可，见 [COPYING](COPYING) 和 [LICENSES/](LICENSES/)。Wi-Fi firmware/NVRAM 需由使用者取得适用于自己模组和板卡的文件及许可，本仓库不分发这些二进制。
