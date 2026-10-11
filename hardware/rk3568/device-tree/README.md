# PatchX RK3568 设备树

[rk3568-patchx-android.dts](rk3568-patchx-android.dts) 是 PatchX `3568A` / AIoT-3568PQ 板卡的设备树参考，描述 RK3568 控制器及屏幕、音频、供电、GPIO 和外设连接。

## 来源与结构

文件由板卡 Android 11、Linux `4.19.232` 运行时 FDT 导出，采集日期为 2026-10-07。它保留扁平设备树中的节点、数字 phandle 和属性，不包含原厂 DTS 的 include、宏、标签和注释结构。

| 功能 | 主要节点 |
| --- | --- |
| 显示与背光 | DSI0、720 × 720 面板、PWM 背光 |
| 板载音频 | I2S、RK809 codec |
| 供电 | RK809、稳压器与 IO 电压域 |
| 身体触摸 | SPI3 上的 CAP1188 |
| 传感器 | I²C 上的 MXC6655 及其他声明节点 |
| 无线连接 | SDIO Wi-Fi、蓝牙相关 GPIO |
| 设备通信 | UART 与 I²C MCU 描述 |

设备树中的节点也包含禁用和备用配置；`status` 与 `compatible` 描述配置，不等同于对应外设已经实装或工作正常。

## 设备信息处理

公开版本移除了序列号、CPU 标识、固定 MAC、部署元数据、启动计数，以及本次启动的 `bootargs`、initrd 地址和两条头部 `/memreserve/`。`/reserved-memory/` 中的硬件内存布局保持原样。

共保留 760 个节点、4,744 项属性；这些属性的字节值均与原始 FDT 一致。移除项、文件校验值和编译检查记录见 [export-info.json](export-info.json)。启动参数和设备身份应由对应设备的启动流程提供。

## 编译查看

安装 Device Tree Compiler（`dtc`）后，在本目录运行：

```sh
dtc -I dts -O dtb -o /tmp/rk3568-patchx-android.dtb rk3568-patchx-android.dts
```

已完成 DTS 编译及逐节点、逐属性对照。反编译树中的数字引用和原有节点格式会产生 DTC 警告；与原始 FDT 的反编译再编译结果相比，警告类别和数量没有增加。

该文件用于硬件接口对照和 BSP 适配。启动相关数据已经移除，不能将编译产物直接当作原厂刷机 DTB；本次未进行刷写或启动验证。

## 许可范围

设备树内容来自原厂系统的运行时硬件描述。原始厂商源码的许可声明未包含在运行时 FDT 中，因此此导出文件不新增或改写厂商内容的许可；仓库根目录 MIT 许可证适用于项目自行编写的文档与代码。
