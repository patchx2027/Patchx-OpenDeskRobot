# Native 工具源码

这些工具由 `apps.patchx` 的相机和 RAM 启动配方使用。Python demo、WebSocket、MQTT 和网页入口不需要先编译它们。第一方原文件及 SHA 见 [SOURCE.json](SOURCE.json)；其中原路径用于说明来源，不是构建依赖。

| 目录 | 入口与用途 |
| --- | --- |
| `launcher/` | `launch-product.c` 为产品 RAM 启动入口；`launch-diagnostic.c` 用于隔离诊断 |
| `jpeg/` | `nv12_to_jpeg.c` 将有限 NV12 帧转成有尺寸上限的 JPEG |
| `camera/finite-helper/` | `aiq_finite.cpp` 管理 ISP 生命周期，`camera_diag.c` 取得一帧；`capture-source/` 为实际使用的第一方采集代码 |
| `camera/finite-supervisor/` | `supervisor.c` 与 `owned-process-cleanup.inc` 管理时限、进程身份及后代清理 |

这里没有预编译程序、RKAIQ 算法档案或模组 IQ。详见 [相机说明](../docs/CAMERA.md)和[第三方依赖](../docs/THIRD_PARTY.md)。

## 编译

在 `software/linux/` 执行。准备 Python 3、GCC/G++ 工具链，以及目标架构的 libc、libjpeg 开发文件。默认编译器前缀是 `aarch64-linux-gnu-`，工具链可从 PATH 或绝对前缀定位。

```sh
python3 tools/build-native.py \
    --cross-prefix aarch64-linux-gnu- \
    --targets launcher supervisor jpeg \
    --output tools/build/arm64-tools
```

`--output` 必须是新目录。产物为 `launch-product`、`launch-diagnostic`、`aiq-supervisor` 和 `nv12-to-jpeg`；同时保存实际编译命令及产物 SHA。脚本不执行它们，也不下载、部署或访问板卡。

这些工具默认静态链接；如果工具链没有对应静态库，可显式选择 `--dynamic-utilities`，并由使用者为目标系统配置动态库。`--sysroot /absolute/target-sysroot` 可覆盖编译器内置 sysroot。JPEG 不在默认搜索路径时使用 `--jpeg-include` 和 `--jpeg-library-dir`，两者都必须指向目标架构的开发文件。

编译 ISP helper 时，另行提供已经适配且头文件与库匹配的 RKAIQ：

```sh
python3 tools/build-native.py \
    --cross-prefix aarch64-linux-gnu- \
    --targets aiq \
    --rkaiq-sdk /absolute/rkaiq-sdk \
    --rkaiq-library /absolute/rkaiq-lib/librkaiq.so \
    --output tools/build/arm64-aiq
```

对应环境变量为 `CROSS_COMPILE`、`SYSROOT`、`RKAIQ_SDK` 和 `RKAIQ_LIBRARY`；命令行参数优先。AIQ 产物 `aiq-finite` 动态链接外部 RKAIQ。构建保留 `ISP_HW_V21` 和实际使用的八组 SDK include 路径，系统调用包装只作用于可执行文件，不改变共享 SDK 的内部 I/O。

仅指定原始 SDK 提交不保证 ABI 匹配。原实测库有 module-info 与 AWB 布局适配，详情见 [CAMERA.md](../docs/CAMERA.md)。编译完成也不会自动更新 Python 相机适配器的输入 SHA。

## 主机 fixture

这些命令是供使用者显式执行的检查入口，本次源码整理未运行它们。需要 Linux 主机、gcc/g++；JPEG 解码检查还需 Pillow。监督器 fixture 创建真实子进程，使用 pidfd、`/proc` 和 `/dev/shm`，用于检查进程回收，不打开板卡。

```sh
python3 tools/build-native.py --cross-prefix '' \
    --host-fixtures --targets supervisor jpeg \
    --output tools/build/host-fixtures
python3 tools/run-host-fixtures.py \
    --build-dir tools/build/host-fixtures --targets supervisor jpeg
```

AIQ fixture 使用提供的 SDK 头文件和本目录的假 SDK，不链接真实 RKAIQ、不采集图像：

```sh
python3 tools/build-native.py --cross-prefix '' \
    --host-fixtures --targets aiq \
    --rkaiq-sdk /absolute/rkaiq-sdk \
    --output tools/build/host-aiq-fixtures
python3 tools/run-host-fixtures.py \
    --build-dir tools/build/host-aiq-fixtures --targets aiq
```

如果环境设置了 `SYSROOT`，先清除此变量再编译主机 fixture。fixture 检查生命周期、30 帧暖机、格式/缓冲区顺序、组合流程及 SDK 共享库 I/O 与可执行文件包装的分离。它们不验证真实 IQ、ISP、3A 或画质。

启动器还有单独的真实 namespace fixture，需要 root、`unshare`、静态宿主 libc 和允许创建 mount/network namespace 的 Linux 环境。它们在私有 namespace 中挂载测试目录、使用本机 socket 和合成环境值；不应在受限容器里把失败改成跳过。

```sh
sudo python3 tools/launcher/test-native-namespace.py --phase green --label diagnostic-1
sudo python3 tools/launcher/test-product-namespace.py --phase green --label product-1
```

每次使用新的 label。结果保存在 `tools/build/launcher-*-tests/`。宿主测试副本只替换固定前缀和内核身份；产品源码中的设备约束保持。它们不是通用服务安装器，也不会生成包含 Python/rootfs 的完整 RAM 包。
