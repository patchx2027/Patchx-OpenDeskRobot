# 从哪里读源码

先选一个功能，找到设备树中的连接，再读对应驱动。修改屏幕时序、GPIO 或供电等板级参数时，从设备树开始；修改启停、错误处理或资源释放时，进入驱动实现。

## 按功能找入口

下表链接直接指向本分支源码。

| 想看什么 | 源码入口 | 先关注什么 |
| --- | --- | --- |
| 产品硬件配置 | [rk3568-patchx-product.dts](../../arch/arm64/boot/dts/rockchip/rk3568-patchx-product.dts) | 原显示/音频入口与相机配置的组合 |
| OV5695 相机 | [ov5695.c](../../drivers/media/i2c/ov5695.c)、[rkisp/](../../drivers/media/platform/rockchip/isp/) | sensor 上下电、CSI2 链路和 ISP 采集 |
| RK809 扬声器播放 | [rk817_codec.c](../../sound/soc/codecs/rk817_codec.c) | codec 参数、控件和播放路由 |
| I2S 音频启停 | [rockchip_i2s_tdm.c](../../sound/soc/rockchip/rockchip_i2s_tdm.c) | 时钟、收发模式和流的启停 |
| PCM 与 DMA 资源 | [soc-pcm.c](../../sound/soc/soc-pcm.c)、[pl330.c](../../drivers/dma/pl330.c) | 打开、准备、停止、释放及失败回滚 |
| DSI 面板 | [panel-simple.c](../../drivers/gpu/drm/panel/panel-simple.c) | 上电、复位、初始化命令及错误返回 |
| USB 音频输入 | [card.c](../../sound/usb/card.c)、[pcm.c](../../sound/usb/pcm.c) | USB 声卡注册与 PCM 采集 |
| Wi-Fi | [bcmdhd/](../../drivers/net/wireless/rockchip_wlan/rkwifi/bcmdhd/) | 固件输入、配置解析与网络接口 |
| 硬件随机数 | [rockchip-rng.c](../../drivers/char/hw_random/rockchip-rng.c) | 设备绑定及随机数读取接口 |

## 设备树文件怎么分工

产品入口及其板级文件都在 `arch/arm64/boot/dts/rockchip/`。从入口看 include 顺序，再按要改的功能打开对应文件；同一节点的后续属性会覆盖先前设置。

| 文件 | 先看哪里 | 适合在这里修改的内容 |
| --- | --- | --- |
| [rk3568-patchx-product.dts](../../arch/arm64/boot/dts/rockchip/rk3568-patchx-product.dts) | 原入口与相机 include | 产品功能组合 |
| [rk3568-patchx-camera.dtsi](../../arch/arm64/boot/dts/rockchip/rk3568-patchx-camera.dtsi) | OV5695、固定电源、DPHY 与 ISP endpoint | 相机接线、有效电平和数据链路 |
| [rk3568-patchx.dts](../../arch/arm64/boot/dts/rockchip/rk3568-patchx.dts) | `/memreserve/` 和 include 顺序 | 板级文件组合、与 loader 配套的保留区 |
| [rk3568-patchx-base.dtsi](../../arch/arm64/boot/dts/rockchip/rk3568-patchx-base.dtsi) | `memory@200000`、`chosen`、USB 与存储节点 | 内存布局、启动参数及基础控制器配置 |
| [rk3568-patchx-power.dtsi](../../arch/arm64/boot/dts/rockchip/rk3568-patchx-power.dtsi) | `vcc3v3_sys`、`vcc5v0_host`、`rk809` | 供电连接和电压约束 |
| [rk3568-patchx-connectivity.dtsi](../../arch/arm64/boot/dts/rockchip/rk3568-patchx-connectivity.dtsi) | `sdio_pwrseq`、`wireless_wlan`、`&sdmmc1` | SDIO、Wi-Fi 供电和唤醒引脚 |
| [rk3568-patchx-audio.dtsi](../../arch/arm64/boot/dts/rockchip/rk3568-patchx-audio.dtsi) | `rk809-sound`、`&i2s1_8ch`、`rk809_codec` | 声卡连接、时钟和音频引脚 |
| [rk3568-patchx-display.dtsi](../../arch/arm64/boot/dts/rockchip/rk3568-patchx-display.dtsi) | `backlight`、`dsi0_panel`、`dsi0_timing` | 背光、面板时序、复位与供电 |
| [rk3568-patchx-panel-init.dtsi](../../arch/arm64/boot/dts/rockchip/rk3568-patchx-panel-init.dtsi) | `&dsi0_panel` 下的命令序列 | 面板控制器的初始化和退出命令 |
| [rk3568-patchx-phandles.dtsi](../../arch/arm64/boot/dts/rockchip/rk3568-patchx-phandles.dtsi) | 固定 phandle 值 | 保持原引用编号，普通参数修改不涉及此文件 |

基础控制器和默认引脚组来自同目录的 `rk3568.dtsi` 及其引用文件。修改板级连接时，先改上表对应文件。

## 阅读声音通路

在 `rk3568-patchx-audio.dtsi` 中找到 `rk809-sound` 节点。它的 `simple-audio-card,cpu` 用 `sound-dai = <&i2s1_8ch>` 连接 CPU 接口，`simple-audio-card,codec` 用 `sound-dai = <&rk809_codec>` 连接 codec。再沿两端的 `clocks`、`pinctrl-0` 查时钟和引脚组。

RK809 路径对应 RK817 codec 驱动和 Rockchip I2S TDM 驱动。现有 checked 音频配置在 I2S 与 codec 两端配对设置 `rockchip,checked-shared-params-48k`，参数为 48 kHz、`S16_LE`、双通道、TRCM=1；CPU 为 I2S 时钟主端，`mclk-fs=256`，codec 时钟为 12.288 MHz。

这条 checked 路径按 48 kHz、双通道 `S16_LE` 实现，不能只改 `simple-audio-card,mclk-fs` 就切换采样率。两端的 checked 属性须配对保留；若要改变支持的参数，需要一起修改 CPU/codec 驱动的参数检查和时钟处理。

想了解一次播放如何收尾，重点沿“配置 → 准备 → 启动 → 停止 → 释放”的过程阅读 codec、I2S 和 PCM/DMA 代码，留意失败后已取得的资源由谁释放。能找到设备连接、参数设置和关闭路径，就读清了这条播放通路。

USB 输入走 `sound/usb/`，与 RK809 的板载 I2S 通路分开。先看 `card.c` 和 `pcm.c`，再按需要读 [endpoint.c](../../sound/usb/endpoint.c)、[stream.c](../../sound/usb/stream.c) 和 [format.c](../../sound/usb/format.c)。实测使用 16 kHz、`S16_LE`、8 通道接口，前 4 个逻辑通道有明确讲话响应。

## 阅读显示配置

打开 `rk3568-patchx-display.dtsi`。先看 `backlight:`，当前配置为：

```dts
pwms = <&pwm4 0 25000 0>;
default-brightness-level = <0>;
```

默认亮度 `0` 是 `brightness-levels` 表的索引；第 0 项也是 0，因此保持启动时背光关闭。它不是直接填写的 PWM 占空比。`pwms` 表示 PWM4、通道 0、周期 25000 纳秒和正常极性。需要调整默认亮度时，根据背光器件选择现有表中的索引；这里保留原值 `0`。

再看 `dsi0_panel:`，沿 `native-mode = <&dsi0_timing>` 找到当前 720×720 时序：

| 参数 | 当前值 |
| --- | --- |
| 像素时钟 `clock-frequency` | `35500000`，即 35.5 MHz |
| `hactive` / `vactive` | `720` / `720` |
| 水平前沿 / 同步宽度 / 后沿 | `24` / `2` / `30` |
| 垂直前沿 / 同步宽度 / 后沿 | `16` / `2` / `8` |
| DSI lane / 格式 | `4` / `MIPI_DSI_FMT_RGB888` |

换屏时，这些值与 `rk3568-patchx-panel-init.dtsi` 中的命令、复位延时及供电必须按同一块面板资料确定。GPIO 示例仍使用现有接线：

```dts
reset-gpios = <&gpio0 RK_PA5 GPIO_ACTIVE_LOW>;
enable-gpios = <&gpio0 RK_PC5 GPIO_ACTIVE_HIGH>;
```

两行分别指 GPIO0_A5 低有效复位和 GPIO0_C5 高有效使能。第三项是有效电平约定，不是引脚当前电平。面板通过 `power-supply = <&vcc3v3_lcd0_n>` 引用同文件的 LCD 电源；该节点的 `3300000` 微伏表示 3.3 V，上游为 `vcc3v3_sys`。

要查驱动如何使用这些参数，打开 `panel-simple.c`，沿属性读取、上电、复位和命令发送过程阅读。改时序和接线用设备树；改初始化错误处理则进入驱动。

## 阅读相机配置

产品入口在原显示/音频配置后引入 `rk3568-patchx-camera.dtsi`。OV5695 位于 I2C2，地址 `0x36`，沿两条 CSI2 data lane 连接 DPHY0，再到 RKISP virtual channel 0。相机固定电源使能使用 GPIO0_D5 高有效，启动等待 20000 微秒；复位使用 GPIO3_D4 低有效，PWDN 使用 GPIO3_D5 低有效。

设备树中的 `reset-gpios` 有效电平与当前 Linux 5.10 `ov5695.c` 的上下电逻辑配合；产品入口采用实测的 flags=1。保留 SDK 原传感器驱动及时序。端点的显式 phandle 及注释对应最后实测的 pre-overlay 设备树，便于逐属性核对。

先读 `ov5695.c` 的 probe、供电与 stream 控制，再读 [phy-rockchip-csi2-dphy.c](../../drivers/phy/rockchip/phy-rockchip-csi2-dphy.c) 和 RKISP 的 media graph、格式协商、缓冲队列与停止路径。内核提供采集通路；有限 JPEG 实测还使用配套用户态 ISP、调参和编码流程，具体条件见 [验证记录](VALIDATION.md#ov5695-相机采集)。

## 电源与引脚参数

`rk3568-patchx-power.dtsi` 中的电压以微伏为单位，例如 `regulator-min-microvolt = <3300000>` 是 3.3 V。通过 `vin-supply` 找上游电源，再通过各设备的 `*-supply` 查看用电端。电压范围按实际供电电路和器件要求修改。

音频文件中的 pinctrl 示例是：

```dts
&i2s1m0_sclktx {
    rockchip,pins = <1 RK_PA3 1 &pcfg_pull_none>;
};
```

四项依次是 bank 1、A3 引脚、复用功能 1、电气配置 `pcfg_pull_none`。这里的第三项是复用编号，与前面的 GPIO 有效电平不是同一种参数。引脚组名称表示用途，具体复用值按 RK3568 引脚定义和板卡接线选择。

## 阅读 Wi-Fi 与随机数驱动

Wi-Fi 从 BCMDHD 目录开始，结合设备树中的 SDIO、供电和唤醒连接阅读。实测网络配置是 `PM=0` 与 `band=a`，使用匹配的 firmware/NVRAM；这两个配置值及 64 KiB 往返结果见 [验证记录](VALIDATION.md#wifi-双向传输)。

随机数驱动从 `rockchip-rng.c` 开始，查看设备匹配、硬件初始化和读取接口。实测确认驱动绑定、CRNG 就绪和非阻塞 `getrandom` 成功。

## 修改后怎么继续

按 [BUILD.md](BUILD.md) 在独立目录中构建，使用同次构建的内核、DTB 和 codec 模块。内存布局、启动参数与 overlay 的配合见其中的“启动适配”一节。
