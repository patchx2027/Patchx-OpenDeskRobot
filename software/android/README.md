# PatchX Android 应用

这是 PatchX 桌面机器人 Android 用户应用的源码发布版，包含语音处理、Unity 交互、视觉处理和设备通信相关代码。当前缺少 `unityLibrary`、Opus Java 适配源码、第三方 SDK、原生库及模型等输入，**本仓库尚不能独立构建完整 APK**。

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

运行依赖设备厂商的音频、串口、板卡服务与原生库，源码还包含对 `com.patchx_sys` 配套服务的绑定接口。这里的最低 API 和 ABI 是构建配置，不代表已验证普通手机、模拟器或任意 Android 板卡均可运行。

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

[excluded-inputs.json](docs/excluded-inputs.json) 记录来源快照中未分发文件的相对路径、大小和 SHA-256。清单用于定位和识别输入，不赋予复制或再分发权，也不保证清单文件补齐后即可编译或运行。私钥、设备授权等材料应由使用者为自己的设备重新取得，不应复用来源工程的材料。

## 本地配置与构建

将 [local.properties.example](local.properties.example) 复制为 `local.properties` 后填写自己的参数。示例默认不提供账户或服务配置；必需参数未配置时，对应连接会记录未配置并跳过。

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

源码发布版缺少必需输入时，检查会列出缺项并以非零状态退出。这是对当前发布边界的提示，不代表完整构建测试。Gradle 配置阶段也会在缺少 Unity 模块时给出说明。

在上述输入与本地配置补齐后，在 `software/android/` 中运行：

```sh
./gradlew :app:assembleDebug
```

Windows 可使用 `gradlew.bat :app:assembleDebug`。首次运行 Wrapper 需要联网下载对应 Gradle 发行版。该命令说明目标构建任务；当前源码发布版不承诺此命令成功。具体执行情况见 [发布状态](docs/RELEASE_STATUS.md)。未经实际构建和设备测试，不将源码检查视为 APK 或硬件功能验收。

## 发布与许可

本版本以原仓库 `ed91ef7486cdebca00aa32d497f5aa45cb226c0e` 的 `PatchX-Android-Main-C/` 为来源，仅导入清理后的文件快照。第一方代码使用根目录 [MIT License](../../LICENSE)；第三方条款和待补证据见 [THIRD_PARTY.md](docs/THIRD_PARTY.md)。
