# 构建内核、设备树与 codec 模块

从仓库根目录执行以下命令。构建使用独立输出目录，生成 Image、PatchX 设备树和与该次内核匹配的 RK817 codec 模块。

## 准备环境

使用 Linux 或 WSL，准备 AArch64 交叉编译器及内核构建依赖，包括 make、binutils、flex、bison、bc、OpenSSL 和 ELF 开发库。原构建使用 `aarch64-linux-gnu-gcc` 11.4.0；下面的命令使用 `aarch64-linux-gnu-` 工具前缀。

还需要 Python 3，并确保 `python` 命令可用：内核的 `scripts/gcc-wrapper.py` 使用 `/usr/bin/env python` 启动。Ubuntu 22.04 可安装 `python-is-python3`，使 `python` 指向 Python 3。

[patchx_rk3568_product.config](../../arch/arm64/configs/patchx_rk3568_product.config) 是树内 codec 构建使用的完整 `.config`，其中 `CONFIG_SND_SOC_RK817=m`。它不是精简 defconfig，按下列步骤复制到输出目录。

本流程用于从本分支生成配套产物。已完成的实机记录使用原 audio-v5 Image、另行构建的树内 codec 模块及相机设备树；本次源码整合仅进行了设备树制品的静态语义对照，没有重新构建或启动完整产品 Image。

## 1. 配置和构建内核

`BUILD_ROOT` 选择尚不存在的目录；以下步骤在同一个 shell 中连续执行。

```sh
KERNEL_SRC="$(pwd)"
BUILD_ROOT="$HOME/patchx-kernel-build"
OUT="$BUILD_ROOT/kernel"

mkdir "$BUILD_ROOT" || exit 1
mkdir "$OUT" || exit 1
cp "$KERNEL_SRC/arch/arm64/configs/patchx_rk3568_product.config" "$OUT/.config" || exit 1

make -C "$KERNEL_SRC" O="$OUT" ARCH=arm64 \
    CROSS_COMPILE=aarch64-linux-gnu- olddefconfig || exit 1

make -C "$KERNEL_SRC" O="$OUT" ARCH=arm64 \
    CROSS_COMPILE=aarch64-linux-gnu- Image modules || exit 1
```

成功后，内核位于 `$OUT/arch/arm64/boot/Image`，RK817 codec 模块位于 `$OUT/sound/soc/codecs/snd-soc-rk817.ko`。`modules` 已通过树内 Kconfig/Makefile 入口构建 codec；保留同次输出目录中的最终 `.config` 和 `Module.symvers`。

## 2. 构建 PatchX 设备树

产品入口为 [rk3568-patchx-product.dts](../../arch/arm64/boot/dts/rockchip/rk3568-patchx-product.dts)，在原显示/音频入口上加入 OV5695 相机配置：

```sh
make -C "$KERNEL_SRC" O="$OUT" ARCH=arm64 \
    CROSS_COMPILE=aarch64-linux-gnu- rockchip/rk3568-patchx-product.dtb || exit 1
```

成功后，产物位于 `$OUT/arch/arm64/boot/dts/rockchip/rk3568-patchx-product.dtb`。此处使用本分支的 Linux 5.10 板级配置。

## 构建完成后

上述命令成功后，应能找到以下产物：

| 产物 | 位置 |
| --- | --- |
| 内核 Image | `$OUT/arch/arm64/boot/Image` |
| PatchX DTB | `$OUT/arch/arm64/boot/dts/rockchip/rk3568-patchx-product.dtb` |
| RK817 codec 模块 | `$OUT/sound/soc/codecs/snd-soc-rk817.ko` |

将最终配置、工具链版本和各产物校验值一起保存。Git 提交和工作树状态会影响内核 release 字符串，因此新分支构建出的 release 可能与原实机记录不同。使用同次构建的 Image 和模块，不要混用旧板上的模块或另一轮构建的符号表。

## 历史构建入口

原 [patchx_rk3568.config](../../arch/arm64/configs/patchx_rk3568.config) 和 [rk3568-patchx.dts](../../arch/arm64/boot/dts/rockchip/rk3568-patchx.dts) 保留原显示/音频构建输入，旧设备树目标仍为 `rockchip/rk3568-patchx.dtb`。

原配置关闭 `CONFIG_SND_SOC_RK817`，旧 codec 通过 [独立模块 Makefile](../../tools/patchx/rk817-module.Makefile) 构建。如需复现旧方式，应在另一个输出目录中使用原配置构建 Image，再复制 codec 文件，使用该旧配置输出目录及其 `Module.symvers`。以下命令中的 `OUT` 和 `BUILD_ROOT` 必须指向那次历史方式构建目录：

```sh
CODEC_OUT="$BUILD_ROOT/rk817-module"
mkdir "$CODEC_OUT" || exit 1
cp "$KERNEL_SRC/sound/soc/codecs/rk817_codec.c" "$CODEC_OUT/" || exit 1
cp "$KERNEL_SRC/sound/soc/codecs/rk817_codec.h" "$CODEC_OUT/" || exit 1
cp "$KERNEL_SRC/tools/patchx/rk817-module.Makefile" "$CODEC_OUT/Makefile" || exit 1
[ -s "$OUT/Module.symvers" ] || exit 1

make -C "$KERNEL_SRC" O="$OUT" ARCH=arm64 \
    CROSS_COMPILE=aarch64-linux-gnu- M="$CODEC_OUT" modules || exit 1
```

## 启动适配

本设备树保留实测 RAM 启动链的内存布局。把产物接入自己的 loader 时，同时查看入口 DTS 和 `rk3568-patchx-base.dtsi`：

| 位置 | 需要与 loader 配合的内容 |
| --- | --- |
| 入口 DTS 的两条 `/memreserve/` | `0x0a100000` 起、长度 `0x25000`；`0x0a200000` 起、长度 `0xc8c20` |
| `memory@200000` | 可见内存分段和高地址映射 |
| `reserved-memory/buffer@1f0000000` | 带 `no-map` 的高地址保留区 |
| `&drm_logo`、`&drm_cubic_lut` | 保留原 loader 分配；当前 Linux 显示配置不接管 logo 映射 |

更换 loader 或 Image、initramfs、FDT 的装载地址时，要重新核对这些范围，避免覆盖保留区或相互重叠。

`chosen` 中使用 `rdinit=/init`，需要配套提供包含 `/init` 的 initramfs，并由它完成所需文件系统挂载和 rootfs 启动。本构建步骤只生成内核、DTB 和模块。

生成的 DTB 对应 pre-overlay 基树。若启动链还会应用 overlay，应核对其内容和应用次数；屏幕、音频等已写入这份源码的配置，不要再通过同内容的合成 overlay 重复应用。
