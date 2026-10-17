# 测试源码

本目录提供 PatchX 应用测试，以及其集成测试所用的 Companion WebSocket fixture。
在 `software/linux` 下安装 `apps/patchx/requirements.txt` 后，可执行：

```sh
python3 -m unittest discover -s tests -p 'test_patchx*.py' -v
```

音频相关测试需要 libopus、libasound 和 ALSA 工具；依赖缺失时应查看实际失败或跳过原因。
这些主机测试使用模拟端口、loopback 网络或文件设备，不等于真实服务和板端功能验收。
本次发布整理未重新运行应用测试、完整内核构建或板端试验。
