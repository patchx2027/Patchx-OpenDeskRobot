# PatchX Android 应用

这里是 PatchX 桌面机器人的 Android 应用代码，包括语音处理、Unity 交互、视觉处理和设备通信。当前缺少 `unityLibrary`、Opus Java 适配源码、第三方 SDK、原生库及模型等输入，**本仓库尚不能独立构建完整 APK**。

## 工程信息

| 项目 | 配置 |
| --- | --- |
| 应用包名 | `com.patchx_main` |
| 版本 | `1.0.1`，`versionCode 101` |
| 最低 Android API | `29` |
| 编译 / 目标 Android API | `36` / `36` |
| 原生库 ABI | `armeabi-v7a` |
| Android Gradle Plugin | `8.11.0` |
| Gradle | `8.13` |
| 构建 JDK | `17` |
| Java 源码 / 字节码级别 | `11` |

运行依赖设备厂商的音频、串口、板卡服务与原生库，源码还包含对 `com.patchx_sys` 配套服务的绑定接口。最低 API 和 ABI 仅表示编译配置；普通手机、模拟器及其他 Android 板卡是否能运行，尚未验证。

## 第一次读代码

先按下面的顺序看三个文件。暂时只读代码时，不必先配置 SDK 或编译 APK。

| 文件 | 先找什么 |
| --- | --- |
| [AndroidManifest.xml](app/src/main/AndroidManifest.xml) | 查看权限、服务和启动 Activity；启动入口是 `.patchx.MainActivity2` |
| [MainActivity2.java](app/src/main/java/com/patchx_main/patchx/MainActivity2.java) | 从 `onCreate()` 看启动初始化；这个 Activity 继承 `UnityPlayerActivity` |
| [DeviceManager.java](app/src/main/java/com/patchx_main/patchx/manage/DeviceManager.java) | 从 `sendDeviceCommand()`、`sendDeviceAngle()` 看设备命令如何调用厂商 UART 接口 |

要了解界面与 Unity 的连接，先读 `MainActivity2`；要改设备命令，先读 `DeviceManager`。涉及配套系统服务时，再查 `app/src/main/aidl/`，然后按下方目录进入对应功能。

## 目录导航

- `app/src/main/java/com/patchx_main/patchx/`：应用入口、设备管理、通信、Unity 与语音流程。
- `app/src/main/java/com/patchx_main/ability/`：语音能力及相关服务适配。
- `app/src/main/java/com/patchx_main/caeLib/`：录音、唤醒及音频前端适配。
- `app/src/main/java/com/patchx/patchface/`、`com/VideoProcess/`：视觉原生接口与调用层。
- `app/src/main/java/com/wcy/testserial/`：串口及固件升级调用层。
- `app/src/main/aidl/`：配套系统服务接口。
- `docs/`：来源、依赖缺口及验证记录。

## 构建前准备

1. 安装 JDK 17 以及 Android SDK Platform 36，使用工程提供的 Gradle 8.13 Wrapper。将 Gradle 使用的 JDK 设置为 17；Java 11 是项目源码级别。工具组合依据 [Android Gradle Plugin 8.11 官方说明](https://developer.android.com/build/releases/agp-8-11-0-release-notes)。
2. 阅读 [第三方说明](docs/THIRD_PARTY.md)，从供应方或权利人合法取得与应用兼容的 SDK、模型和资源。按清单路径补齐本地输入，保留供应方提供的许可和版本信息。
3. 将对应 Unity 项目导出为 Android library，放入 `software/android/unityLibrary/`。当前来源快照没有该工程，Unity 版本、场景资源及与 Java 层的消息协议仍需由原项目维护者补齐，不能用空目录代替。
4. 将 `local.properties.example` 复制为 `local.properties`，设置本机 `sdk.dir`，并填写工程使用的 `patchx.*` 属性。属性通过 Gradle 生成的 `BuildConfig` 注入应用；本机配置文件不应提交。

SDK、原生库和资源的预期位置如下，路径相对于本目录：

| 输入 | 位置 |
| --- | --- |
| Opus Java 适配源码 | `app/src/main/java/com/theeasiestway/opus/` 中的 3 个文件；见第三方说明 |
| Unity Android library | `unityLibrary/` |
| Java SDK / AAR | `app/libs/` |
| 32 位 ARM 原生库 | `app/src/main/jniLibs/armeabi-v7a/` |
| 语音模型、视觉模型及配套资源 | `app/src/main/assets/` 下的对应路径 |

[excluded-inputs.json](docs/excluded-inputs.json) 记录来源快照中未分发文件的相对路径、大小和 SHA-256。可用这份清单核对文件。文件的使用和再分发仍受原许可约束，补齐清单也不表示编译和运行已经通过。私钥、设备授权等材料应由使用者为自己的设备重新取得，不应复用来源工程的材料。

## 本地配置与构建

[local.properties.example](local.properties.example) 中各参数的用途如下。填写自己的账户和服务信息；必需参数为空时，应用会记录缺项并跳过对应连接。

| 属性 | 用途 |
| --- | --- |
| `patchx.websocketUrl` | 自有 WebSocket 服务地址 |
| `patchx.accessToken` | WebSocket 服务访问令牌 |
| `patchx.clientId` | 客户端标识 |
| `patchx.deviceSn` | 自己设备的序列号 |
| `patchx.userId` | 自己服务中的用户标识 |
| `patchx.mqttHost` | 自有 MQTT 服务主机；连接使用 TLS 端口 `8883` |
| `patchx.mqttInstanceId` | MQTT 实例标识 |
| `patchx.mqttAccessKeyId` | MQTT 接入凭据 ID |
| `patchx.mqttAccessKeySecret` | MQTT 接入凭据 Secret |
| `patchx.mqttGroupId` | MQTT 分组标识 |

`BuildConfig` 值会进入 APK，不能把这种配置方式当作运行时秘密保护机制。请勿分发含生产密钥的 APK；生产环境应使用服务端签发的有限权限凭据。

可以先运行缺失输入检查，路径相对于本工程：

```sh
python3 tools/check_prerequisites.py
```

有缺项时，脚本会列出文件并以非零状态退出。先处理这些缺项，再尝试构建；这一步只检查输入，不会编译 APK。Gradle 也会在缺少 Unity 模块时给出说明。

在上述输入与本地配置补齐后，在 `software/android/` 中运行：

```sh
./gradlew :app:assembleDebug
```

Windows 可使用 `gradlew.bat :app:assembleDebug`。首次运行 Wrapper 需要联网下载对应 Gradle 发行版。当前仓库仍缺少开头列出的输入，尚未通过完整 APK 构建。已有检查结果见 [发布状态](docs/RELEASE_STATUS.md)；源码检查不能替代 APK 构建和设备测试。

## 发布与许可

本版本以原仓库 `ed91ef7486cdebca00aa32d497f5aa45cb226c0e` 的 `PatchX-Android-Main-C/` 为来源，仅导入清理后的文件快照。第一方代码使用根目录 [MIT License](../../LICENSE)；第三方条款和待补证据见 [THIRD_PARTY.md](docs/THIRD_PARTY.md)。
