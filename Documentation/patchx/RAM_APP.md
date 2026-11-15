# PatchX App 随 RAM Linux 自动启动

这套流程适用于[已实测固件组合的 3568A](RAM_BOOT.md)，通过原厂 Android 暂存文件，使用原厂 U-Boot 启动 Linux，随后由 initramfs 自动运行同一份 PatchX 用户程序入口 `python3 -m apps.patchx`。测试结束通过串口停止应用并重启回 Android。启动代码见 [init](../../software/linux/deploy/patchx/ram/init) 和 [PatchX 启动脚本](../../software/linux/deploy/patchx/ram/patchx-ram-start)。已有 Linux 用户空间时，可按[手动启动说明](../../software/linux/docs/GETTING_STARTED.md)直接运行应用。

## 1. 准备构建输入

先按[Linux RAM 启动](RAM_BOOT.md)备份原厂 `boot`、`dtbo`，构建产品 Image、DTB 和 RK817 模块。沿用其中的 `BACKUPS`、`OUT`、`BUILD_ROOT` 和静态 ARM64 `BUSYBOX`。BusyBox 还需提供 `chroot`、`ip`（含 link 操作）、`gzip`、`tar`、`sha256sum`、`grep`、`cp`、`mkdir`、`rm` 与 `umount`，以及 `mount --bind` 和 `--install -s`。

另备一个自己的 ARM64 Linux 运行环境，归档为 `runtime.tar.gz`，顶层目录必须为 `runtime/`：

- 提供 `runtime/bin/sh`、`runtime/usr/bin/python3`、对应动态加载器、系统库和 Python 标准库。将[应用依赖](../../software/linux/apps/patchx/requirements.txt)安装到该解释器可见的目录。使用板端音频还需要 ALSA 工具、libasound、ALSA 配置和 libopus。
- 使用同一 ARM64 根文件系统内配套工具和库，保留符号链接，链接目标使用归档树内的相对路径。运行环境只需 App 依赖，归档内提供 `runtime/opt/` 目录，并给自启动脚本留出 `runtime/opt/patchx-ram-app/` 路径。
- 本流程从受信任的本地运行目录打包。归档压缩前常规文件总量最多 80 MiB，运行时会在 RAM 中同时展开 tar 流与文件树，建议使用精简运行环境。板端 `/cache` 需要能容纳 40 MiB 启动包和独立的运行归档，另留文件系统余量。也可复用板上已存在且完整校验的同一归档。

例如，将 `RUNTIME_PARENT` 设为已准备好的 `runtime/` 的父目录，再归档。

```sh
tar --numeric-owner --owner=0 --group=0 -czf "$BUILD_ROOT/runtime.tar.gz" \
    -C "$RUNTIME_PARENT" runtime
```

## 2. 打包应用与启动镜像

在仓库根目录执行。默认配置为演示模式，网页只绑定到板端回环地址。可用 `--config` 指定自己的 PatchX JSON。运行环境、服务凭据和可选外设设置见[应用安装与配置](../../software/linux/docs/GETTING_STARTED.md)。

```sh
CACHE_PATH=patchx-ram-app/runtime.tar.gz
tools/patchx/build-rk3568-patchx-initramfs.py \
    --busybox "$BUSYBOX" \
    --codec "$OUT/sound/soc/codecs/snd-soc-rk817.ko" \
    --runtime-archive "$BUILD_ROOT/runtime.tar.gz" \
    --cache-path "$CACHE_PATH" \
    --output "$BUILD_ROOT/patchx-app-initramfs.cpio.gz"

tools/patchx/build-rk3568-ram-boot.py \
    --stock-boot "$BACKUPS/boot.img" \
    --stock-dtbo "$BACKUPS/dtbo.img" \
    --image "$OUT/arch/arm64/boot/Image" \
    --dtb "$OUT/arch/arm64/boot/dts/rockchip/rk3568-patchx-product.dtb" \
    --kernel-release "$(cat "$OUT/include/config/kernel.release")" \
    --initramfs "$BUILD_ROOT/patchx-app-initramfs.cpio.gz" \
    --output "$BUILD_ROOT/patchx-app-ram.img"
```

initramfs 包含当前检出的 `apps/patchx`、`apps/companion`、配置和自启动脚本。ARM64 运行环境仍是独立的缓存文件。自启动脚本以 `ro,noload` 挂载 `cache` 分区，核对归档 SHA-256 并解包到 RAM，然后卸载缓存。应用在 RAM 中使用该用户空间运行。

## 3. 在 Android 暂存，再用 U-Boot 启动

```sh
adb shell 'mkdir /cache/patchx-ram-app'
adb push "$BUILD_ROOT/runtime.tar.gz" "/cache/$CACHE_PATH"
adb push "$BUILD_ROOT/patchx-app-ram.img" /cache/patchx-ram-app/boot.img
adb shell 'sync; sha256sum /cache/patchx-ram-app/runtime.tar.gz /cache/patchx-ram-app/boot.img'
```

核对两个 SHA-256。按[Linux RAM 启动第 4 节](RAM_BOOT.md#4-从-android-暂存再由-u-boot-启动)进入 U-Boot。把示例里的暂存文件路径改成 `/patchx-ram-app/boot.img`。保持实测地址和启动参数，读取 40 MiB 并核对本轮 CRC32 后才执行 `bootm 20000000`。

## 4. 检查自启动并返回 Android

Linux 串口会显示 `PATCHX_RAM_READY`，应用派生后显示 `PATCHX_RAM_APP_LAUNCHED`。出现 PatchX 的监听地址后，执行：

```sh
patchx-ram-status
```

该命令会检查 App 进程、首页和 `/api/status`。出现 `PATCHX_RAM_APP_READY` 表示业务服务已经可用，应用无需手工启动。如果自启动失败，串口 shell 仍可用于查看错误。

完成测试后，在板端串口执行：

```sh
patchx-ram-return
```

脚本给 PatchX 发 TERM，等待 Python 进程退出，卸载应用用户空间的挂载，确认板载块设备文件系统均已卸载，再重启。进入原厂 Android 后可清理本轮暂存文件。

## 3568A 测试板实测范围

3568A 测试板从原厂 Android 暂存后启动 40 MiB RAM 包，CRC32 为 `1ae3e12c`。PatchX 未经手动执行即监听在 `127.0.0.1:8090`，首页与状态接口可用，并通过 HTTP 完成一次演示对话。结束时用 `patchx-ram-return` 返回原厂 Android。`boot`、`dtbo` 和 `uboot` 分区的 SHA-256 前后一致。这次验收使用演示配置，云端语音及各外设的使用条件见[用户程序说明](../../software/linux/README.md)。
