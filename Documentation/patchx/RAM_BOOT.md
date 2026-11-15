# 旧版 3568A RAM 启动包

[打包入口](../../tools/patchx/build-rk3568-ram-boot.py) 生成给旧版 3568A 原厂 U-Boot 使用的 40 MiB 临时启动包。先按[内核构建说明](BUILD.md)生成同次构建的 Image、产品 DTB 和 RK817 模块。

## 输入

在 Linux 或 WSL 安装 Python 3.9 或更新版本和 `device-tree-compiler`，确保 `fdtoverlay`、`fdtget` 在 PATH 中。

- **板卡备份**。使用从自己旧版 3568A 只读备份的完整原厂 `boot`、`dtbo` 分区。工具核对已实测的固件 SHA-256，包括 Boot v2 头、Rockchip 资源和板卡 overlay。其它固件版本需要单独适配。
- **内核产物**。使用本分支产品配置生成的未压缩 ARM64 `Image`、`rk3568-patchx-product.dtb` 和 `include/config/kernel.release`。DTB 使用应用原厂 overlay 之前的版本。
- **早期用户空间**。提供 gzip 压缩的 `newc` initramfs，内有可执行的根目录 `/init` 和同次构建的 `snd-soc-rk817.ko`。工具核对 ARM64 模块和 vermagic 的 release。启动脚本负责挂载 `/dev`、`/proc`、`/sys`，并根据用途提供串口 shell、加载模块或启动根文件系统。可在准备好的 initramfs 目录里用 `find . -print0 | cpio --null -o -H newc --owner 0:0 | gzip -n` 归档。为脚本式 `/init` 提供 ARM64 解释器，例如静态 BusyBox 和指向它的 `/bin/sh`。PatchX 应用、根文件系统和外设固件由使用者提供。

## 组装

从仓库根目录运行。这里沿用[内核构建说明](BUILD.md)的 `OUT`，其余路径指向实际输入和一个尚不存在的输出文件。

```sh
tools/patchx/build-rk3568-ram-boot.py \
  --stock-boot "$BACKUPS/boot.img" \
  --stock-dtbo "$BACKUPS/dtbo.img" \
  --image "$OUT/arch/arm64/boot/Image" \
  --dtb "$OUT/arch/arm64/boot/dts/rockchip/rk3568-patchx-product.dtb" \
  --kernel-release "$(cat "$OUT/include/config/kernel.release")" \
  --initramfs "$INITRAMFS" \
  --output "$BUILD_ROOT/patchx-ram.img"
```

工具保留原厂资源中的 logo，用产品 DTB 替换资源内 DTB，并用备份的 overlay 做离线兼容检查。输出中的 DTB 由板上 loader 应用 overlay。成功时输出大小、CRC32 和 SHA-256。

## 临时启动

把启动包作为普通文件暂存在旧板可由 U-Boot 读取的文件系统，校验 SHA-256。进入 U-Boot 后先核对板型、`bdinfo` 和分区表，确认 RAM 和暂存位置。以下地址对应旧板实测布局，分区和文件路径按本次暂存位置填写。

1. 用 `ext4load` 把包读到 `0x20000000`。核对 `filesize=0x2800000`，并执行 `crc32 20000000 2800000` 比对打包输出。
2. 在本次 U-Boot 会话设定 `kernel_addr_r=400000`、`fdt_addr_r=a100000`、`ramdisk_addr_r=4000000`，并将 `initrd_high`、`fdt_high` 设为 `ffffffffffffffff`。本次 Linux 启动参数为 `console=ttyFIQ0 earlycon=uart8250,mmio32,0xfe660000 rdinit=/init ro loglevel=7 panic=0`。
3. 执行 `bootm 20000000`。这套流程只读入 RAM，环境设置仅用于当前会话。进入 Linux 后核对内核版本和功能，结束时卸载临时挂载并重启回原厂 Android。

使用 2026 年 11 月 15 日实测的同组输入，打包入口生成了与旧板实测镜像逐字节一致的文件，CRC32 为 `9f9ec0e9`。该次实测范围见[内核验证说明](VALIDATION.md#2026-年-11-月-15-日构建与旧板验证)。
