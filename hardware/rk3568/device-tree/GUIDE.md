# 如何阅读和修改设备树

第一次读可以从背光开始：它只涉及面板、背光节点和 PWM 控制器，比较容易顺着引用找到位置。文中的代码片段都取自当前配置。

## 按要做的事找文件

| 要做的事 | 打开文件，搜索这个名字 | 本文对应位置 |
| --- | --- | --- |
| 调整默认亮度 | `board/display.dtsi` → `backlight:` | [默认背光亮度](#backlight) |
| 查看或更换屏幕参数 | `board/display.dtsi` → `dsi0_panel:`、`dsi0_timing:` | [屏幕配置](#display) |
| 查看屏幕初始化命令 | `board/panel-init.dtsi` → `panel-init-sequence` | [屏幕配置](#display) |
| 了解扬声器接在哪个接口 | `board/audio.dtsi` → `rk809_sound:` | [声音连接](#audio) |
| 核对电压或 GPIO 接线 | `board/power.dtsi`、`rk3568-android-pinctrl.dtsi` | [电源与引脚](#pins) |

先打开 [总入口](rk3568-patchx-android.dts)，看板型和 `/include/` 引入了哪些文件。然后选上表中的一项进入 `board/`，遇到控制器引用再查 `base`，遇到引脚组再查 `pinctrl`。不用先读完这两个大文件。

## 先认清标签与引用

```dts
backlight: backlight {
    /* 背光配置 */
};
```

冒号前的 `backlight` 是标签，后面的 `backlight` 是节点名。其他节点使用 `<&backlight>` 指向它。`&dsi0 { ... };` 则是在已有 `dsi0` 节点上补充属性，控制器的基础定义可到 [base 文件](rk3568-android-base.dtsi) 中查找。

`compatible` 决定匹配哪类驱动，`status = "okay"` 表示在此配置中启用节点；引脚、电源和相连设备通过引用连起来。查找一个功能时，先找到它的板级节点，再沿 `*-supply`、`pinctrl-0`、`sound-dai` 或 `remote-endpoint` 查看连接。

<a id="backlight"></a>

## 找到默认背光亮度

打开 [board/display.dtsi](board/display.dtsi)，找到 `backlight:`：

```dts
pwms = <&pwm4 0 25000 0>;
/* brightness-levels 的完整亮度表见源文件。 */
default-brightness-level = <128>;
```

`default-brightness-level = <128>` 选择表中从 0 开始计数的第 128 个索引，即第 129 项，**不是 128% 或直接的 PWM 占空比**。驱动使用所选表值计算 PWM 输出；改变默认索引和改变整张亮度表是两种不同操作。

`pwms` 指向 PWM4，后面的 `0` 是通道号，`25000` 是周期（纳秒），最后的 `0` 表示这里采用的正常极性。PWM 周期、极性和亮度曲线应符合实际背光驱动器；默认索引必须落在现有表的范围内。

在文件里沿这条顺序找一遍：

1. `default-brightness-level` 选择哪一项。
2. `brightness-levels` 中该项及相邻项如何变化。
3. `pwms` 指向哪个控制器，周期与极性是什么。
4. 面板的 `backlight = <&backlight>` 是否连接到这个背光节点。

要调整开机亮度，修改的就是这个默认索引，当前值为 `128`。先根据背光器件确定目标亮度，保留完整亮度表，再选择对应的表项。

<a id="display"></a>

## 读懂 720×720 屏幕配置

仍在 [board/display.dtsi](board/display.dtsi)，找到 `dsi0_panel:` 下的 `display-timings`：

```dts
native-mode = <&dsi0_timing>;
```

它选中 `dsi0_timing:`。当前参数如下：

| 属性 | 值 | 含义 |
| --- | --- | --- |
| `clock-frequency` | `35500000` | 像素时钟 35.5 MHz |
| `hactive` / `vactive` | `720` / `720` | 水平与垂直有效像素 |
| `hback-porch` | `30` | 水平后沿间隔 |
| `hsync-len` | `2` | 水平同步宽度 |
| `hfront-porch` | `24` | 水平前沿间隔 |
| `vback-porch` | `8` | 垂直后沿间隔 |
| `vsync-len` | `2` | 垂直同步宽度 |
| `vfront-porch` | `16` | 垂直前沿间隔 |

水平间隔以像素时钟计，垂直间隔以行计。文件中的 `dsi0_timing_default:` 是另一组保留时序；当前选用的是 `native-mode` 指向的 `dsi0_timing:`，也就是 720×720 这组。

换屏时，分辨率只是其中一项。按屏厂资料和实际接线一起核对：

| 位置 | 需要由实际器件确定的内容 |
| --- | --- |
| `dsi0_timing:` | 像素时钟、有效尺寸、前后沿、同步宽度及极性 |
| `dsi0_panel:` | `dsi,lanes`、数据格式与 DSI flags；当前为 4 条 lane |
| `dsi0_panel:` 的 GPIO 与延时 | reset/enable 引脚、有效电平、上电和复位顺序 |
| `power-supply = <&vcc3v3_lcd0_n>` | 屏的供电连接与电压 |
| [board/panel-init.dtsi](board/panel-init.dtsi) | 面板控制器对应的初始化命令 |

初始化文件使用 `&dsi0_panel` 给同一面板补充命令。例如原有第一行：

```dts
13 02 02 ff 30
```

这些是十六进制字节：类型 `0x13`、延时 `0x02` 毫秒、载荷长度 `0x02`，然后是两个载荷字节 `ff 30`。一行对应一条命令；命令内容和延时由面板控制器资料决定。

看完这一节，应能找到当前选中的时序、面板命令、复位引脚和供电节点。显示连接可沿 `route_dsi0` → `vp1_out_dsi0` / `dsi0_in_vp1` → `dsi_out_panel` / `panel_in_dsi` 阅读。endpoint 两端的 `remote-endpoint` 指向对端，用来描述显示通路。

<a id="audio"></a>

## 沿声卡找到 I2S 与 codec

打开 [board/audio.dtsi](board/audio.dtsi)，从 `rk809_sound:` 开始：

```text
rk809_sound
  ├─ simple-audio-card,cpu   → i2s1_8ch
  └─ simple-audio-card,codec → rk809_codec（位于 rk809 下）
```

两个子节点的 `sound-dai` 分别引用 CPU I2S 接口和 codec。沿标签继续看：

| 标签 / 属性 | 当前配置与作用 |
| --- | --- |
| `rk809_sound` 的 `simple-audio-card,format` | `i2s`，连接格式 |
| `rk809_sound` 的 `simple-audio-card,mclk-fs` | `256`，主时钟与采样率的倍频关系 |
| `i2s1_8ch` 的 `rockchip,clk-trcm` | `1`，厂商 I2S 驱动的时钟收发模式配置 |
| `i2s1_8ch` 的 `pinctrl-0` | 引用 SCLK、LRCK、SDI、SDO 引脚组 |
| `rk809_codec` 的 `assigned-clock-rates` | `12288000`，12.288 MHz 时钟配置 |
| `rk809_codec` 的 `pinctrl-0` | 引用 `i2s1m0_mclk` 引脚组 |
| `rk809_sound` 的 `simple-audio-card,routing` | 声卡端点与 codec 端点之间的路由名称 |

在 48 kHz 下，256 倍关系对应 12.288 MHz。更换 codec、采样时钟方案或实际连线时，应同时核对两端驱动绑定、时钟来源、引脚及路由名称，不能只改声卡名称。

从声卡的两个 `sound-dai` 出发，能找到 I2S、codec，以及它们各自的时钟和引脚组，就读清了这段连接。

这里说明 Android 4.19 的现有连接。Linux 5.10 checked 音频路径还有配对属性及固定参数要求，按 [Linux 驱动说明](../drivers/linux-5.10/README.md) 集成。

<a id="pins"></a>

## 电源、GPIO 与 pinctrl 怎么读

在 [board/power.dtsi](board/power.dtsi) 查找 `vcc3v3_lcd0_n:`：

```dts
regulator-min-microvolt = <3300000>;
regulator-max-microvolt = <3300000>;
vin-supply = <&vcc3v3_sys>;
```

电压单位是微伏，`3300000` 对应 3.3 V；min/max 相同表示这里描述固定电压。`vin-supply` 指向上游供电，面板的 `power-supply` 再指向这一路稳压器。电压、使能方式和电源时序由板卡电路与器件额定要求决定。

GPIO 与 pinctrl 使用不同的元组格式。面板已有的 GPIO 例子是：

```dts
reset-gpios = <&gpio0 5 1>;
enable-gpios = <&gpio0 21 0>;
```

按此控制器的 GPIO 格式，三部分分别是控制器引用、bank 内引脚号、标志。`&gpio0 5 1` 对应 GPIO0_A5、低有效；`&gpio0 21 0` 对应 GPIO0_C5、高有效。有效电平由外围电路决定，并不是写入的当前电平。

再沿 `i2s1_8ch` 的 `pinctrl-0` 打开 [rk3568-android-pinctrl.dtsi](rk3568-android-pinctrl.dtsi)，找到 `i2s1m0_sclktx:`：

```dts
rockchip,pins = <0x1 0x3 0x1 &pcfg_pull_none>;
```

四项依次是 bank 1、bank 内引脚 3、复用功能编号 1、电气配置 `pcfg_pull_none`。它选择 GPIO1_A3 的对应复用功能并引用无上下拉配置；第三项是 mux 编号，不是 GPIO 有效电平。具体 mux 与电气配置按 RK3568 引脚定义和接线核对。

## 修改后的检查

每次围绕一个硬件功能修改对应 `board/` 文件，编译入口 DTS，检查新增诊断，并在匹配的内核与板卡上核对功能。编译通过说明语法和引用能被处理；参数改动是否适合硬件，要在板上确认。此前的逐项对照只针对整理前后的原参数。`rk3568-android-compat.dtsi` 用于保留原句柄和符号路径，普通板级参数修改通常不涉及它。

## 延伸阅读

- [Raspberry Pi overlays README](https://github.com/raspberrypi/firmware/blob/master/boot/overlays/README)：按名称查找功能与参数说明的文档组织示例，参数本身适用于其平台。
- [Armbian 构建入口](https://github.com/armbian/build/blob/main/README.md)：快速开始和进一步文档的导航示例。
- [Linux DTS 编码风格](https://docs.kernel.org/devicetree/bindings/dts-coding-style.html)：标签、节点组织及板级引用的编写约定。
