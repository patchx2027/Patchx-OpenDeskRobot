"""Bounded diagnostics. Never accept free-form messages, transcripts or argv.

Use emit(code) for app events; snapshot(source, level, limit) returns sanitized
entries. System entries are classified kernel events, not raw kernel log lines.
"""
from collections import deque
from datetime import datetime, timezone
import os
import re
import selectors
import subprocess
import threading
import time

_TEMPLATES = {
    "service_started": ("info", "服务启动；麦克风默认关闭"),
    "continuous_started": ("info", "连续对话已开启"),
    "microphone_muted": ("info", "麦克风已关闭"),
    "microphone_enabled": ("info", "麦克风已允许开启"),
    "browser_lease_expired": ("warning", "浏览器控制租约到期，已停止采音"),
    "session_failed": ("error", "语音会话失败，已停止录放音"),
    "echo_quarantined": ("warning", "疑似扬声器回声已隔离，未提交回答"),
    **{"state_" + key: ("info", text) for key, text in {
        "offline": "语音服务离线", "connecting": "正在准备语音服务",
        "muted": "语音就绪，麦克风关闭", "idle": "语音就绪",
        "listening": "正在监听", "thinking": "正在处理请求",
        "speaking": "正在播放回答", "error": "语音服务进入错误状态"}.items()},
}
_LEVELS = {"info": 0, "warning": 1, "error": 2}


def _kernel_read():
    """At most 128 KiB/0.8 s; stderr and raw output never reach diagnostics."""
    proc = None
    try:
        proc = subprocess.Popen(["dmesg", "-s", "65536"], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, close_fds=True)
        data = bytearray()
        deadline = time.monotonic() + .8
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while len(data) < 131072 and time.monotonic() < deadline:
                ready = selector.select(max(0., deadline - time.monotonic()))
                if not ready:
                    break
                chunk = os.read(proc.stdout.fileno(), min(8192, 131072 - len(data)))
                if not chunk:
                    return bytes(data).decode("utf-8", "replace"), proc.wait(timeout=.1) == 0
                data.extend(chunk)
        return bytes(data).decode("utf-8", "replace"), bool(data)
    except (OSError, subprocess.TimeoutExpired):
        return "", False
    finally:
        if proc:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            proc.stdout.close()


def _kernel_entries(raw):
    # Allowlist subsystem markers and conditions; output ONLY fixed templates.
    entries = []
    subsystems = (("memory", r"out of memory|oom-kill|killed process", "内存"),
                  ("npu", r"rknpu|rknn", "NPU"),
                  ("audio", r"alsa|asoc|snd_|es8389", "音频"),
                  ("thermal", r"thermal|overheat", "温度"),
                  ("network", r"ethernet|rk_gmac|stmmac|wlan", "网络"))
    for line in raw.splitlines():
        lower = line.lower()
        for code, pattern, label in subsystems:
            if not re.search(pattern, lower):
                continue
            if re.search(r"error|fail|timeout|timed out|out of memory|oom-kill|killed process|overheat", lower):
                level, condition = "error", "异常事件"
            elif re.search(r"warn|underrun|overrun|link is down|link down", lower):
                level, condition = "warning", "警告事件"
            elif re.search(r"init|registered|initialized|link is up|link up", lower):
                level, condition = "info", "初始化或连接事件"
            else:
                break
            stamp = re.match(r"\s*\[\s*([0-9.]+)\]", line)
            entries.append(dict(id=len(entries) + 1, timestamp="boot+" + stamp.group(1) + "s" if stamp else "",
                                level=level, source="system", code=code + "_" + level,
                                message=label + condition + "（内核日志摘要）"))
            break
    return entries[-256:]


class Diagnostics:
    def __init__(self, capacity=256, system_reader=None):
        self.capacity = max(1, min(256, int(capacity)))
        self.entries = deque(maxlen=self.capacity)
        self.lock = threading.Lock()
        self.system_lock = threading.Lock()
        self.sequence = 0
        self.system_reader = system_reader or _kernel_read
        self.system_cache = None
        self.system_at = -100.

    def emit(self, code):
        if code not in _TEMPLATES:
            return  # Never echo unrecognized input into a log.
        level, message = _TEMPLATES[code]
        with self.lock:
            self.sequence += 1
            self.entries.append(dict(id=self.sequence,
                timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                level=level, source="app", code=code, message=message))

    def snapshot(self, source="app", level="all", limit=100):
        if source not in ("app", "system") or level not in ("all", *_LEVELS):
            raise ValueError("Unsupported diagnostic filter")
        limit = max(1, min(self.capacity, int(limit)))
        supported = True
        if source == "app":
            with self.lock:
                entries = [dict(entry) for entry in self.entries]
        else:
            with self.system_lock:
                if self.system_cache is None or time.monotonic() - self.system_at >= 5:
                    raw, supported = self.system_reader()
                    self.system_cache = (_kernel_entries(raw) if supported else [], supported)
                    self.system_at = time.monotonic()
                cached, supported = self.system_cache
                entries = [dict(entry) for entry in cached]
        if level != "all":
            entries = [entry for entry in entries if _LEVELS[entry["level"]] >= _LEVELS[level]]
        return dict(source=source, entries=entries[-limit:], capacity=self.capacity,
                    supported=supported, available=supported,
                    reason="" if supported else "无法读取内核日志，可能权限不足或工具不可用",
                    sanitized=True)
