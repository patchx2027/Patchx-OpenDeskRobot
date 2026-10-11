# RK3568 Linux 5.10 驱动

本目录按已验证功能公开 PatchX RK3568 板卡使用的 Linux 驱动补丁与源码索引。

| 功能 | 已验证范围 |
| --- | --- |
| RK809 扬声器播放 | 板载扬声器短音播放，现场确认可听见 |
| PCM 资源生命周期 | 10 个有限阶段，覆盖打开/关闭、单向传输及双向启停 |
| USB 音频采集 | 16 kHz、`S16_LE`、8 通道接口，前 4 个逻辑通道具有明确讲话响应 |
| DSI 显示 | 720×720 彩条显示，结束后恢复像素与背光 |
| Wi-Fi | `PM=0`、`band=a` 的 5 GHz 配置下，64 KiB 双向 TCP 传输一致 |
| 硬件随机数 | Rockchip RNG 绑定、CRNG 就绪与非阻塞 `getrandom` 成功 |

条件和结果见 [VALIDATION.md](VALIDATION.md)，机器可读清单见 [features.json](features.json)。

## 源码与许可

固定基线为 [Orange Pi Linux BSP](https://github.com/orangepi-xunlong/linux-orangepi.git) 提交 `9f9e9d18574d0914c0d192a90c3babfe1fd63c95`，版本 `5.10.160-rt89`。

[series](series) 按顺序列出 15 份补丁：`0001`–`0014` 中排除传感器补丁 `0005`，随后应用 `0015`、`0016`。USB Audio 采用固定 SDK 原有实现。补丁及 42 个源文件的身份记录在 [manifest.json](manifest.json)：25 个改动文件、17 个上游引用文件。

驱动文件与各自实测来源一致；排除传感器补丁后的完整组合未重新构建。本发布提供源码级集成，不作为已验收的整机镜像。

补丁许可继承被修改文件，参见 [COPYING](COPYING)、[LICENSES/](LICENSES/) 和 [SOURCE-NOTICES.txt](SOURCE-NOTICES.txt)。Wi-Fi firmware/NVRAM 由使用者取得适用于自己模组和板卡的文件及许可，本目录不分发这些二进制。

## 从哪里读代码

下列路径相对固定版本的内核源码目录；先应用本项目补丁，再阅读对应实现。

| 功能 | 主要源码入口 | 本项目改动 |
| --- | --- | --- |
| RK809 播放 | `sound/soc/codecs/rk817_codec.c` | codec 参数、控件和生命周期 |
| I2S 与 DMA | `sound/soc/rockchip/rockchip_i2s_tdm.c`、`drivers/dma/pl330.c` | 启停、错误处理及 PCM 资源管理 |
| 屏幕 | `drivers/gpu/drm/panel/panel-simple.c` | 初始化错误处理；控制器仍由 BSP 提供 |
| USB 采集 | `sound/usb/card.c`、`sound/usb/pcm.c` | 使用固定上游实现 |
| Wi-Fi | `drivers/net/wireless/rockchip_wlan/rkwifi/bcmdhd/` | 独立输出目录的头文件搜索路径 |
| 随机数 | `drivers/char/hw_random/rockchip-rng.c` | 使用固定上游实现 |

硬件连线从 [设备树阅读与修改教程](../../device-tree/GUIDE.md) 开始，驱动的 `compatible`、时钟、GPIO 和供电引用应与对应 BSP 的设备树一致。

## 应用补丁

在 Linux 或 WSL 中，从本目录执行。示例创建独立工作目录，按 `series` 逐条检查并应用补丁；任一步失败即退出。完整源码从上述固定上游取得。

```sh
PATCHSET="$(pwd)"
WORK="$HOME/patchx-linux-work"
BASE=9f9e9d18574d0914c0d192a90c3babfe1fd63c95

mkdir "$WORK" || exit 1
git clone https://github.com/orangepi-xunlong/linux-orangepi.git "$WORK/linux" || exit 1
git -C "$WORK/linux" checkout --detach "$BASE" || exit 1

actual_base="$(git -C "$WORK/linux" rev-parse HEAD)" || exit 1
[ "$actual_base" = "$BASE" ] || exit 1

while IFS= read -r patch || [ -n "$patch" ]; do
    case "$patch" in
        ''|'#'*) continue ;;
    esac
    git -C "$WORK/linux" apply --check "$PATCHSET/$patch" || exit 1
    git -C "$WORK/linux" apply "$PATCHSET/$patch" || exit 1
done < "$PATCHSET/series"
```

补丁使用 `git apply`，不要使用 `git am`。应用后可按 `manifest.json` 核对结果源码。

## 构建内核与 codec

准备 AArch64 交叉编译环境及内核构建依赖；原构建使用 GCC 11.4.0。[audio-v5.config](config/audio-v5.config) 提供原构建配置。沿用上一节变量：

```sh
OUT="$WORK/build"
mkdir "$OUT" || exit 1
cp "$PATCHSET/config/audio-v5.config" "$OUT/.config" || exit 1

make -C "$WORK/linux" O="$OUT" ARCH=arm64 \
    CROSS_COMPILE=aarch64-linux-gnu- olddefconfig || exit 1

make -C "$WORK/linux" O="$OUT" ARCH=arm64 \
    CROSS_COMPILE=aarch64-linux-gnu- Image modules || exit 1
```

配置关闭 `CONFIG_SND_SOC_RK817`，codec 通过 [rk817-module.Makefile](config/rk817-module.Makefile) 独立构建。使用同一份已应用补丁的源码、同次 Image 的构建输出和 `Module.symvers`：

```sh
CODEC_OUT="$WORK/rk817-module"
mkdir "$CODEC_OUT" || exit 1
cp "$WORK/linux/sound/soc/codecs/rk817_codec.c" "$CODEC_OUT/" || exit 1
cp "$WORK/linux/sound/soc/codecs/rk817_codec.h" "$CODEC_OUT/" || exit 1
cp "$PATCHSET/config/rk817-module.Makefile" "$CODEC_OUT/Makefile" || exit 1
[ -s "$OUT/Module.symvers" ] || exit 1

make -C "$WORK/linux" O="$OUT" ARCH=arm64 \
    CROSS_COMPILE=aarch64-linux-gnu- M="$CODEC_OUT" modules || exit 1
```

保留最终 `.config`、工具链版本及产物散列，使用同次构建的 Image 和模块。上述步骤生成内核与模块，板级设备树和启动包按实际硬件集成。

## 音频设备树

实测组合为 I2S1 与 RK809/RK817 codec。以下两个节点须配对设置空布尔属性 `rockchip,checked-shared-params-48k`：

- `/i2s@fe410000`
- `/i2c@fdd40000/pmic@20/codec`

参数为 48 kHz、`S16_LE`、双通道、TRCM=1；simple-card 使用 I2S，CPU 为时钟主端，`mclk-fs=256`，codec 时钟为 12.288 MHz。时钟、供电、DMA 和路由同时按板卡配置。

仓库中的 [Android 设备树参考](../../device-tree/README.md) 来自 Android 4.19 运行树，不含该 checked 属性。Linux 5.10 集成使用匹配的板级设备树。
