"""Read-only face/status and local resource sampler. Never opens the camera."""
import json
import math
import os
import resource
import re
import threading
import time
import urllib.request

class Monitor:
    def __init__(self, core):
        self.core = core
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="companion-monitor", daemon=True)
        self.last_frame = None
        self.last_progress = 0.
        self.last_cpu = time.process_time()
        self.last_wall = time.monotonic()
        self.worker_sample = None
        self.system_cpu_sample = None
        self.clock_ticks = os.sysconf("SC_CLK_TCK")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def start(self):
        self.thread.start()

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=3)

    def sample_face(self):
        url = self.core.config["face_url"]
        if not url:
            return {"available": False, "reason": "未配置视觉服务"}
        try:
            with self.opener.open(url, timeout=1) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("oversized face response")
            data = json.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get("faces", []), list):
                raise ValueError("invalid face response")
            frame = data.get("frame")
            if type(frame) is not int:
                raise ValueError("missing frame sequence")
            now = time.monotonic()
            if frame != self.last_frame:
                self.last_progress, self.last_frame = now, frame
            fresh = now - self.last_progress <= self.core.config["face_stale_s"]
            available = fresh and data.get("running") is True
            faces = []
            for face in data.get("faces", [])[:32]:
                if not isinstance(face, dict) or not isinstance(face.get("name"), str):
                    continue
                score = face.get("similarity")
                if type(score) not in (int, float) or not math.isfinite(score):
                    continue
                faces.append({"name": face["name"][:128], "similarity": score})
            fps = data.get("fps", data.get("publish_fps"))
            if type(fps) not in (int, float) or not math.isfinite(fps):
                fps = None
            return {"available": available, "running": available, "reason": "" if available else "视觉帧已停滞",
                    "frame": frame, "fps": fps, "faces": faces if available else [],
                    "age_s": round(now - self.last_progress, 2)}
        except Exception:
            self.last_frame = None
            return {"available": False, "reason": "视觉服务不可用", "faces": []}

    @staticmethod
    def read_process(pid):
        # comm may contain spaces and parentheses; fields after its final ')' start at state (3).
        with open("/proc/%d/stat" % pid) as stream:
            fields = stream.read().rsplit(")", 1)[1].split()
        ticks = int(fields[11]) + int(fields[12])
        started = int(fields[19])
        threads = int(fields[17])
        values = {}
        with open("/proc/%d/status" % pid) as stream:
            for line in stream:
                if line.startswith(("VmRSS:", "VmHWM:")):
                    values[line.split(":", 1)[0]] = int(line.split()[1]) / 1024
        return started, ticks, threads, values

    def sample_worker(self, now):
        worker = getattr(self.core, "speech_worker", None)
        if worker is None:
            self.worker_sample = None
            return {}
        metrics = {"local_speech_running": worker.poll() is None,
                   "local_speech_rss_mb": None, "local_speech_peak_rss_mb": None,
                   "local_speech_cpu_percent": None, "local_speech_threads": None}
        if not metrics["local_speech_running"]:
            self.worker_sample = None
            return metrics
        try:
            started, ticks, threads, memory = self.read_process(worker.pid)
            identity = (worker.pid, started)
            previous = self.worker_sample
            self.worker_sample = (identity, ticks, now)
            if previous and previous[0] == identity and now > previous[2] and ticks >= previous[1]:
                # Single-core percent: a two-core inference may legitimately exceed 100%.
                metrics["local_speech_cpu_percent"] = round(
                    100 * (ticks - previous[1]) / self.clock_ticks / (now - previous[2]), 2)
            metrics["local_speech_threads"] = threads
            metrics["local_speech_rss_mb"] = round(memory["VmRSS"], 2) if "VmRSS" in memory else None
            metrics["local_speech_peak_rss_mb"] = round(memory["VmHWM"], 2) if "VmHWM" in memory else None
        except (OSError, ValueError, IndexError):
            self.worker_sample = None
        return metrics

    def sample_system_cpu(self):
        metrics = {"system_cpu_percent": None, "cpu_logical_cores": None}
        try:
            with open("/proc/stat") as stream:
                lines = stream.read(65536).splitlines()
            fields = lines[0].split()
            if fields[0] != "cpu" or len(fields) < 9:
                raise ValueError("invalid aggregate CPU counters")
            # guest/guest_nice are already included in user/nice. Count only fields 1..8.
            counters = tuple(int(value) for value in fields[1:9])
            cores = sum(bool(re.fullmatch(r"cpu[0-9]+", line.split()[0]))
                        for line in lines if line.split())
            if any(value < 0 for value in counters) or not cores:
                raise ValueError("invalid CPU counters or core count")
            metrics["cpu_logical_cores"] = cores
            previous = self.system_cpu_sample
            self.system_cpu_sample = (cores, counters)
            if previous is not None and previous[0] == cores:
                delta = tuple(value - old for value, old in zip(counters, previous[1]))
                total = sum(delta)
                if total > 0 and all(value >= 0 for value in delta):
                    # idle and iowait are not execution; the aggregate already sums all cores.
                    metrics["system_cpu_percent"] = round(
                        100 * (total - delta[3] - delta[4]) / total, 2)
        except (OSError, ValueError, IndexError):
            self.system_cpu_sample = None
        return metrics

    @staticmethod
    def sample_npu():
        # Board driver debugfs is read-only here; never mount it or alter permissions.
        try:
            with open("/sys/kernel/debug/rknpu/load") as stream:
                value = stream.read(256)
            match = re.fullmatch(r"\s*NPU load:\s*([0-9]{1,3})%\s*", value)
            if match and 0 <= int(match[1]) <= 100:
                return {"npu_load_percent": int(match[1])}
        except OSError:
            pass
        return {"npu_load_percent": None}

    @staticmethod
    def sample_temperature():
        values = []
        for index in range(8):
            try:
                with open("/sys/class/thermal/thermal_zone%d/temp" % index) as source:
                    value = float(source.read(32)) / 1000
                if math.isfinite(value) and -40 <= value <= 150:
                    values.append(value)
            except (OSError, ValueError):
                pass
        return {"temperature_c": round(max(values), 1) if values else None}

    def run(self):
        while not self.stop_event.is_set():
            self.core.post("face", self.sample_face())
            now, cpu = time.monotonic(), time.process_time()
            metrics = {"cpu_percent": round(100 * (cpu-self.last_cpu) / max(.001, now-self.last_wall), 2),
                       "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2)}
            try:
                with open("/proc/self/status") as stream:
                    for line in stream:
                        if line.startswith("VmRSS:"):
                            metrics["rss_mb"] = round(int(line.split()[1]) / 1024, 2)
                with open("/proc/meminfo") as stream:
                    memory = {line.split(":")[0]: int(line.split()[1]) for line in stream}
                metrics["memory_total_mb"] = round(memory["MemTotal"] / 1024)
                metrics["memory_used_mb"] = round((memory["MemTotal"]-memory["MemAvailable"]) / 1024)
            except (OSError, ValueError, KeyError):
                pass
            metrics.update(self.sample_system_cpu())
            metrics.update(self.sample_npu())
            metrics.update(self.sample_temperature())
            metrics.update(self.sample_worker(now))
            with self.core.lock:
                self.core.data["metrics"].update(metrics)
            self.last_cpu, self.last_wall = cpu, now
            self.stop_event.wait(self.core.config["face_poll_s"])
