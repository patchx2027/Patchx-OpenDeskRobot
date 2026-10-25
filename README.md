# PatchX OpenDeskRobot

PatchX 是一个桌面机器人项目。`android` 分支提供 Android 应用源码和设备树参考。新的 [Linux 分支](https://github.com/patchx2027/Patchx-OpenDeskRobot/tree/linux)提供完整内核、板级设备树、Linux 用户程序和原生辅助工具。

Android 代码位于 [`software/android/`](software/android/README.md)。Linux 的功能、配置和实测结果见 [Linux 说明](https://github.com/patchx2027/Patchx-OpenDeskRobot/blob/linux/README.md)。

## 从哪里开始

第一次接触项目，先选自己要做的事，不必从头读完整个仓库。

| 想做什么 | 先看这里 | 接着看什么 |
| --- | --- | --- |
| 了解 Android 应用如何组织 | [Android 工程说明](software/android/README.md) | 应用入口和对应功能的 Java 代码 |
| 调整屏幕亮度、时序或硬件接线 | [设备树教程](hardware/rk3568/device-tree/GUIDE.md) | 教程对应的 `board/` 文件 |
| 阅读或修改 Linux 驱动 | [驱动源码导航](hardware/rk3568/drivers/linux-5.10/README.md) | 进入 Linux 分支，按功能读源码和构建教程 |
| 准备编译 Android 应用 | [构建前准备](software/android/README.md#构建前准备) | 先核对第三方依赖，再填写本地配置 |

本分支设备树参考使用 Android 4.19，Linux 分支使用 Linux 5.10。开始修改前，先确认自己使用的内核版本。

### Linux 在线语音

2026 年 10 月 25 日，Linux 主机客户端使用 `ws://118.31.102.180:8360/` 完成文字和合成语音各三轮问答，收到正确答案及可解码音频。Linux 分支已更新对应配置示例；板端真实麦克风收音和扬声器播放尚未重新验收。具体条件见[在线语音说明](https://github.com/patchx2027/Patchx-OpenDeskRobot/blob/linux/software/linux/docs/ONLINE_VOICE.md)，服务结果限定于当次测试条件。

此前的 [`kernel` 分支](https://github.com/patchx2027/Patchx-OpenDeskRobot/tree/kernel)保留原内核发布版本。

### 文档索引

- [Android 工程说明与构建准备](software/android/README.md)
- [RK3568 设备树](hardware/rk3568/device-tree/README.md)
- [设备树阅读与修改教程](hardware/rk3568/device-tree/GUIDE.md)
- [RK3568 Linux 驱动与验证记录](hardware/rk3568/drivers/linux-5.10/README.md)
- [第三方依赖与待补输入](software/android/docs/THIRD_PARTY.md)
- [发布范围、来源与验证状态](software/android/docs/RELEASE_STATUS.md)

## 目录

```text
software/android/     Android 应用源码、资源与构建配置
hardware/rk3568/       Android 设备树参考与 Linux 内核源码入口
LICENSE               第一方代码的 MIT 许可证
qr-group.png          微信讨论群二维码
```

Android 源码来自原仓库提交 `ed91ef7486cdebca00aa32d497f5aa45cb226c0e` 中的 `PatchX-Android-Main-C/`，以清理后的文件快照导入；本仓库保留自身历史，未导入原 Android 仓库历史。

## 参与项目

欢迎通过 Issues 说明问题、补充复现条件，通过 Pull Request 改进代码和文档。涉及 SDK、模型或其他外部资源时，请一并提供来源、版本和适用许可；请勿提交账户凭据、私钥、设备授权文件或个人数据。

微信讨论群 / WeChat Group：扫描下方二维码，若已失效请通过仓库 Issues 联系维护者。

<img src="qr-group.png" width="280" alt="PatchX OpenDeskRobot 微信群二维码 / WeChat group QR code">

## 许可证

第一方代码采用 [MIT License](LICENSE)。第三方代码、SDK、模型和素材遵循各自的许可，原有第三方声明应予保留；本仓库的 MIT 许可证不替代其授权条款。尚待核对的资源记录在 [第三方清单](software/android/docs/THIRD_PARTY.md)，当前未随源码分发。
