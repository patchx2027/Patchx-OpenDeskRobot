# PatchX RK3568 设备树

本目录描述 PatchX `3568A` / AIoT-3568PQ 板卡的 RK3568 控制器与屏幕、音频、供电及外设连接。设备树按硬件功能拆分，引用使用 `&label`，常用板级配置集中在 `board/`。

内容由 2026-10-07 采集的 Android 11 / Linux `4.19.232` 运行树整理而来，标签根据原导出符号恢复，并非取得了原厂 DTS 源文件。

## 快速开始

先打开入口 [rk3568-patchx-android.dts](rk3568-patchx-android.dts)，根据 include 顺序找到对应功能。安装 Device Tree Compiler（`dtc`）后，在本目录编译：

```sh
dtc -i . -I dts -O dtb \
    -o /tmp/rk3568-patchx-android.dtb \
    rk3568-patchx-android.dts
```

编译产物用于检查和集成。整理前后的 760 个节点、4,744 项保留属性已逐项核对等价；该语义核对不等于新系统上板验收。来源、脱敏项及校验值见 [export-info.json](export-info.json)。

## 按这个顺序阅读

| 文件 | 阅读内容 |
| --- | --- |
| [rk3568-patchx-android.dts](rk3568-patchx-android.dts) | 总入口和文件组织 |
| [board/display.dtsi](board/display.dtsi) | 背光、DSI 路由、面板及显示时序 |
| [board/panel-init.dtsi](board/panel-init.dtsi) | 面板初始化命令，一条命令一行 |
| [board/audio.dtsi](board/audio.dtsi) | 声卡 → I2S1 → RK809 codec 的连接 |
| [board/power.dtsi](board/power.dtsi) | 供电电压、上下游电源与 RK809 PMIC |
| [board/connectivity.dtsi](board/connectivity.dtsi) | SDIO、Wi-Fi 与蓝牙连接 |
| [rk3568-android-pinctrl.dtsi](rk3568-android-pinctrl.dtsi) | 板级配置引用的引脚组与电气配置 |
| [rk3568-android-base.dtsi](rk3568-android-base.dtsi) | 控制器、地址、中断和其他基础节点 |
| [rk3568-android-compat.dtsi](rk3568-android-compat.dtsi) | 保留原句柄与导出符号路径，通常无需修改 |

## 阅读与修改指南

[GUIDE.md](GUIDE.md) 从现有配置出发，介绍默认亮度、720×720 屏时序、声音连线，以及电压、GPIO 和 pinctrl 元组的读法。示例保留当前值，实际修改根据器件资料和板卡接线确定。

这是 Android 4.19 的硬件参考；Linux 5.10 驱动集成的参数与设备树要求见 [驱动说明](../drivers/linux-5.10/README.md)。

## 来源与许可

公开内容已移除设备身份和本次启动数据，保留硬件节点及 `/reserved-memory/` 布局，具体记录见 [export-info.json](export-info.json)。设备身份和启动参数由目标设备的启动流程提供。

运行时 FDT 不携带原厂源码许可声明，因此本目录不新增或改写厂商内容的许可；仓库根目录 MIT 许可证适用于项目自行编写的文档与代码。
