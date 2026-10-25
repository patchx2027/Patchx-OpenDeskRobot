# 开始使用

先在 Linux 电脑上运行演示，再在板端配置音频和在线服务。所有 Python 命令都从 `software/linux` 执行。

## 获取源码和依赖

```sh
git clone --branch linux --single-branch https://github.com/patchx2027/Patchx-OpenDeskRobot.git
cd Patchx-OpenDeskRobot/software/linux
python3 --version
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r apps/patchx/requirements.txt
```

需要 Python 3.10 或更新版本。Ubuntu 22.04 可通过系统包安装 Python 和 venv：

```sh
sudo apt install python3 python3-venv python3-pip
```

`apps/patchx/requirements.txt` 引入 Companion 的依赖，固定使用 `websocket-client==1.8.0`、`qrcode==8.2` 和 `paho-mqtt==2.1.0`。不要将原设备的私有配置或凭据复制进公开目录。

## 运行演示

```sh
python3 -m apps.patchx --config apps/patchx/demo.json
```

浏览器打开 <http://127.0.0.1:8090/>，操作准备语音、麦克风和说话按钮，查看状态变化。演示不采集麦克风、不连接后端，也不会产生真实声音。能打开页面并看到演示状态变化，便完成了这一步。按 Ctrl+C 停止。

每次打开新终端，都先进入 `software/linux` 并执行 `. .venv/bin/activate`。

## 配置在线服务

先阅读 [在线语音状态](ONLINE_VOICE.md)。模板采用 2026 年 10 月 25 日已通过主机问答验证的 `8360` 服务配置；使用者仍需具备该服务的使用授权，并填写自己的有效身份和令牌。

将模板复制到仓库外：

```sh
install -d -m 700 "$HOME/.config/patchx"
install -m 600 deploy/patchx/live.example.json "$HOME/.config/patchx/live.json"
```

用编辑器修改这份本地 JSON。服务地址已填写，设备、客户端标识和 IP 仍是占位值，必须替换。MQTT 的 `example.invalid` 不指向实际服务，`192.0.2.10` 不能用作本机 IP。`patchx.audio_enabled=false` 只表示不启用 RK809 板级音频适配；live 模式仍可能通过通用音频路径打开配置设备。若只想看界面而不接触设备，请使用 demo。

| 配置项 | 填写内容 |
| --- | --- |
| `voice.mode` | `live` |
| `voice.voice_backend` | `android`，表示当前实现的兼容协议 |
| `voice.backend_url` | 模板使用 `ws://118.31.102.180:8360/`；将 URL 中 `device_id`、`user_id` 占位值替换为同一应用 SN 的 URL 编码结果，令牌不放入 URL |
| `voice.device_id` | 与两个 URL 参数相同的应用 SN，这里填未做 URL 编码的原值；不能任意把板卡硬件序列号当作已登记身份 |
| `voice.client_id` | 服务方要求的客户端标识 |
| `voice.token_env` | `PATCHX_VOICE_TOKEN`，这里只填写环境变量名 |
| `voice.listen_mode` | 保持本次成功组合的 `realtime` |
| `voice.websocket_heartbeat` | 模板保持 `false`，见 [心跳说明](ONLINE_VOICE.md#主动心跳的修正) |
| `patchx.client_ip` | 板端当前实际 IP，必须是有效 IP 字面量 |

布尔值使用 JSON 的 `true` / `false`，不能写成字符串。字段定义分别见 [Companion 配置](../apps/companion/config.py) 和 [PatchX 配置](../apps/patchx/config.py)。

模板已显式设置 `voice.allow_insecure_ws=true`，因为已验证地址使用明文 `ws://`。身份、令牌和音频没有 TLS 保护；更换为自己的 `wss://` 服务时可设回 `false`。

程序不会自动替换 URL 中的占位符。请将两个参数填成与 `voice.device_id` 相同的应用 SN，并对每个参数值进行 URL 编码（例如 `+` 写成 `%2B`、`&` 写成 `%26`）；`voice.client_id` 是另一个独立字段。带这两个参数的组合已完成语音验证，但对照试验未证明参数必需，见 [验证说明](ONLINE_VOICE.md#10-月-25-日验证了什么)。

上传格式保持 16 kHz 单声道、每包 60 ms 的 Opus。服务在本次 hello 中声明 24 kHz 输出，由客户端按协商结果解码；不需要将麦克风采样率改为 24 kHz。

在 Bash 中输入本次运行的令牌，输入内容不会回显：

```bash
read -r -s -p '服务令牌：' PATCHX_VOICE_TOKEN
printf '\n'
export PATCHX_VOICE_TOKEN
python3 -m apps.patchx --config "$HOME/.config/patchx/live.json"
unset PATCHX_VOICE_TOKEN
```

不要将令牌值写进 JSON、命令参数或提交记录。程序启动时保持离线和麦克风静音；在页面准备语音后才连接服务。看到已连接只说明连接阶段完成，识别文字、回答和实际声音需要分别观察。

## 接入板端硬件

真实录放音需要板卡上的 ALSA 驱动、libasound、libopus，以及 `arecord`、`aplay`、`amixer`。Ubuntu/Debian 系统可安装：

```sh
sudo apt install libopus0 libasound2 libasound2-plugins alsa-utils
```

精简 rootfs 需要提供同等库、工具及 ALSA 配置。电脑上的演示成功不会确认板端声卡可用。

先按 [板端音频](../deploy/patchx/AUDIO.md) 核对声卡，再设置：

- `voice.capture_device="patchx_capture"`。
- `voice.playback_device="patchx_playback"`。
- `patchx.audio_enabled=true`。
- `patchx.volume_initial` 为 0–15 的软件播放音量，默认 8。

启动前在同一个终端设置：

```sh
export ALSA_CONFIG_PATH="$PWD/deploy/patchx/alsa-rk3568.conf"
```

使用这些设置后，取消静音并操作说话按钮才会开始采集板端麦克风。浏览器不会采集电脑麦克风。

其余硬件按需逐项开启：

| 开关 | 同时需要核对 |
| --- | --- |
| `patchx.backlight_enabled` | `backlight_path` 是包含亮度属性的绝对 sysfs 目录 |
| `patchx.display_enabled` | `display_device` 指向匹配的 720×720、32 位 framebuffer |
| `patchx.camera_enabled` | `camera_program` 指向可执行包装脚本的绝对路径，不能直接指向 `aiq-finite`；按[包装入口说明](CAMERA.md#应用使用的是-wrapper-接口)配置参数与运行清单，`camera_work_dir` 使用绝对路径 |
| `patchx.mqtt_enabled` | broker、端口、TLS 与三个 MQTT 环境变量名称按自己的服务填写 |

MQTT 用户名、密码、客户端 ID 分别通过 `mqtt_username_env`、`mqtt_password_env`、`mqtt_client_id_env` 指定的环境变量提供。相机编译与配置见 [相机说明](CAMERA.md)，源码在 [tools/camera](../tools/camera/)。供应方库和 IQ 文件不由 Python 依赖安装命令提供。

## 从电脑访问板端页面

保持服务监听 loopback，在电脑建立 SSH 隧道；把 `USER@BOARD` 替换成自己的登录目标：

```sh
ssh -N -L 8090:127.0.0.1:8090 USER@BOARD
```

电脑浏览器访问 <http://127.0.0.1:8090/>。停止时先结束说话或播放，再在应用终端按 Ctrl+C；退出后不要保留不再使用的令牌环境变量。