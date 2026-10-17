# 第三方依赖与来源

`software/linux` 中的第一方程序沿用其 MIT 许可；内核仍保留内核自己的许可。这里没有把第三方 SDK、模型、固件或二进制重新授权为 MIT。

## Python 与系统运行库

| 依赖 | 本程序使用的版本或接口 | 获取方式 |
| --- | --- | --- |
| Python | 原生运行包使用 Python 3.10 | 目标系统发行版提供的 Python |
| websocket-client | `1.8.0` | `apps/companion/requirements.txt` |
| qrcode | `8.2` | 同上 |
| paho-mqtt | `2.1.0` | `apps/patchx/requirements.txt` |
| libopus | C API，经 ctypes 使用 | 目标系统开发/运行包 |
| ALSA | libasound、arecord、aplay、amixer | 目标系统 ALSA 包 |
| libjpeg-turbo | 原 ARM64 编码器依赖 Ubuntu `2.1.2-0ubuntu1` | 目标架构开发包，或使用者自行构建 |
| Pillow | JPEG 主机 fixture 的独立解码器 | 仅运行该 fixture 时安装 |
| NumPy / sherpa-onnx / RKNN 及语音模型 | Companion 可选本地语音路线 | 不属于当前 PatchX 在线语音的必需输入；未在此打包 |

依赖安装文件保留各自版本约束。第三方包的许可证、NOTICE、二进制对应源码等要求以所取得的包为准；仓库没有复制其 wheel、deb、动态库、算法档案或模型。`tools/build-native.py` 不自动下载依赖。

## RKAIQ 与模组 IQ

SDK 来源：

- 仓库：TinkerBoard `rockchip-linux-external-camera_engine_rkaiq`
- 上游地址：https://github.com/TinkerBoard/rockchip-linux-external-camera_engine_rkaiq
- 固定提交：`143e8227562713df6965480cfd0ff9464d44442d`
- 版本：Linux RKAIQ `v3.0x8.3`，ARM64 / ISP21

实测采用该代 Linux AE/AWB 算法档案，加上 [CAMERA.md](CAMERA.md) 所述 R4 ABI 适配；Android ELF32 库没有被直接用作 Linux 库。公开可访问的上游仓库不等于其中所有预编译算法档案都已确认具备本项目需要的再分发许可。本次没有完成这些档案的再分发授权确认，因此 SDK、算法档案和 `librkaiq.so` 均由使用者自备。

模组 IQ `ov5695_TongJu_CHT842-MD.json` 来自原设备配置，其再分发授权未确认，也未随源码发布。以下 SHA 用于识别历史实测输入，不是下载入口或许可声明：

| 输入 | SHA-256 |
| --- | --- |
| R4 `librkaiq.so` | `3ecc983fd76e4f795e0072071c365754c86bbb12d5878af869d45e1e4cfd12d8` |
| 原模组 IQ JSON | `8619ad612c492c64079ee5e9a012d8474bf4b396e611b615aa16ab8b3e52ce72` |

第一方 native helper、受限 module-info 转换头和 fixture 源码在 `tools/` 中可直接阅读。使用外部 SDK 时保留其文件声明，不将未知许可的档案或原设备 IQ 混入第一方源码包。

## 不随源码发布的内容

设备身份、云端口令/令牌、设备配置、照片/音频、原始板端日志、厂商算法档案、提取的 IQ、固件和历史 RAM 镜像均不在这个用户程序目录中。相机、在线语音及可选本地语音的外部输入需要分别配置；源码发布不表示这些服务或运行输入已经随仓库提供。
