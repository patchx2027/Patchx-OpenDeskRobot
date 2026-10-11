# 第三方依赖与待补输入

本文件记录源码发布时已识别的外部输入及证据缺口。第一方代码的 MIT 许可证不替代第三方许可；第三方代码已有的版权、许可和来源注释应予保留。表中的“待核对”表示尚未收齐来源、版本或授权证据，不构成对该组件禁止分发的结论。

## 未随源码分发的输入

来源快照中的 `app/libs/`、`app/src/main/jniLibs/` 和 `app/src/main/assets/` 未整体复制进公开源码。逐文件清单见 [excluded-inputs.json](excluded-inputs.json)，其中包含大小和 SHA-256，以便与合法取得的文件比对。

| 组件 / 资源 | 本地预期位置 | 仍需补充的信息 |
| --- | --- | --- |
| Opus Java 适配源码 | `app/src/main/java/com/theeasiestway/opus/` | 3 个文件未提供来源及许可证据，暂缓导入；需确认适配版本与源码授权 |
| Unity Android library | `unityLibrary/` | 源工程缺失；需确认 Unity 版本、导出设置、场景素材及授权 |
| AIUI、录音与 CAE Java SDK | `app/libs/AIUI.jar`、`AlsaRecorder.jar`、`cae.jar` | 供应方、SDK 版本、接口兼容性和再分发条款 |
| 音乐 SDK | `app/libs/kwmusic-autosdk-v2.0.9.jar`、`kwylsdk.jar` | SDK 与内容服务分别适用的授权及接入要求 |
| 板卡 SDK | `app/libs/smdt.jar` | 对应设备、SDK 版本和供应方许可 |
| 原生音频 / 视觉 / 串口库 | `app/src/main/jniLibs/armeabi-v7a/` | 每个二进制的源码或供应方、版本、构建方法与许可 |
| RKNN 模型 | `app/src/main/assets/models/` | 原始模型来源、训练权重许可、转换配置及目标芯片 |
| ONNX / 唤醒 / VAD 模型 | `app/src/main/assets/`、`hlw/`、`hlw_near/`、`vad/`、`vtn/` | 模型版本、来源和许可；SDK 所需授权材料 |
| 音频、图片及配置资源 | `app/src/main/assets/` 下原路径 | 作者或供应方、适用许可及是否包含设备 / 账户信息 |

原生库清单包含 RKNN、ONNX Runtime、OpenCV、Opus、sherpa、TEN VAD、OVR LipSync 及设备专用桥接库等名称。文件名只能帮助识别，不能据此认定实际版本或许可证。自定义原生库也需要补齐来源或可复现构建信息。

`auth.ini`、`vtn/privateKey.pem` 和相关设备授权材料不随本发布提供。使用者需向对应供应方取得自己的授权材料，按实际 SDK 要求配置；请勿将私钥或授权材料加入公共版本控制。

## 随仓库提供的构建工具

工程提供 Gradle 8.13 的官方 Wrapper 脚本与 JAR；其许可文本保存在 [`gradle/LICENSE`](../gradle/LICENSE)。Wrapper JAR 与官方下载的 SHA-256 已比对，Gradle 分发包的 SHA-256 固定在 `gradle/wrapper/gradle-wrapper.properties` 中。Gradle 工具的许可独立于第一方应用代码的 MIT 许可证。

## Gradle 声明的依赖

下表来自 `app/build.gradle` 和 `gradle/libs.versions.toml`，用于定位依赖声明。具体解析结果和传递依赖尚需在可构建环境中生成完整清单；以下不是已完成的许可审计。

| 依赖 | 声明版本 |
| --- | --- |
| AndroidX AppCompat / Activity / ConstraintLayout | `1.7.1` / `1.8.0` / `2.2.1` |
| Material Components | `1.12.0` |
| AndroidX WorkManager | `2.9.0` |
| AndroidX CameraX core / camera2 / lifecycle / view | `1.3.4` |
| Logger | `2.2.0` |
| OkHttp | `4.12.0` |
| EventBus | `3.3.1` |
| Gson | `2.8.2` |
| Eclipse Paho MQTT client | `1.2.5` |
| hannesa2 Paho Android | `4.4.1` |
| UUID Creator | `5.3.6` |
| JUnit / AndroidX Test JUnit / Espresso | `4.13.2` / `1.2.1` / `3.6.1` |

准备二进制发布时，需根据实际解析的组件与版本保留其适用的 LICENSE、NOTICE 和署名文件，并核查传递依赖。版本目录中仅声明但未使用的条目，不应被当作已进入 APK 的组件。

## 源码中的外部适配层

以下三个文件使用第三方包名，来源快照中未找到对应来源或许可声明，已暂缓导入，并登记在 [excluded-inputs.json](excluded-inputs.json)：

- `app/src/main/java/com/theeasiestway/opus/Opus.java`
- `app/src/main/java/com/theeasiestway/opus/OpusEncoder.java`
- `app/src/main/java/com/theeasiestway/opus/OpusDecoder.java`

应用仍引用这些接口，补齐第三方二进制不能代替上述 Java 源码。恢复这些文件前，需要记录上游项目、对应版本或提交、适用许可及本地修改，并核对与 `libeasyopus.so` 的接口兼容性。该暂缓决定表示本次未核齐证据，不表示这些文件一定不能分发。

## 如何补齐

1. 向组件的供应方或权利人取得适用于自己设备和用途的文件与许可，记录版本及获取来源。
2. 按上表和 JSON 清单的相对路径放到本地工程，核对 ABI、设备、SDK 与模型的匹配关系。
3. 在公开补充文件前记录许可依据；允许再分发的文件仍须保留要求的许可和声明，不允许或尚未确认的文件继续由使用者自行取得。
4. 补齐构建输入后，另行验证 APK 构建、设备运行和业务服务配置，更新 [发布状态](RELEASE_STATUS.md)。
