package com.patchx_main.patchx.mqtt;

import android.os.Handler;
import android.os.HandlerThread;

import com.patchx_main.patchx.manage.DeviceManager;

import java.util.concurrent.atomic.AtomicBoolean;

public class StatusReporter {
    private final AliMqttClient mqtt;
    private final String topic;
    private final int qos;
    private final boolean retain;
    private final long periodMs;

    private final AtomicBoolean started = new AtomicBoolean(false);
    private HandlerThread worker;
    private Handler handler;

    private final Runnable task = new Runnable() {
        @Override public void run() {
            if (!started.get()) return;
            if (mqtt.isConnected()) {
                try {
                    DeviceManager.getInstance().updateDeviceInfo();
                    mqtt.publish(topic, UnifiedRobotState.i().toJson(), qos, retain);
                } catch (Throwable ignore) {}
            }
            handler.postDelayed(this, periodMs);
        }
    };

    public StatusReporter(AliMqttClient mqtt, String topic, int qos, boolean retain, long periodMs) {
        this.mqtt = mqtt;
        this.topic = topic;
        this.qos = qos;
        this.retain = retain;
        this.periodMs = Math.max(5_000L, periodMs);
    }

    /** 后台线程启动 + 立即执行一次 */
    public void start() {
        if (!started.compareAndSet(false, true)) return;
        worker = new HandlerThread("StatusReporter");
        worker.start();
        handler = new Handler(worker.getLooper());
        handler.post(task);
    }

    /** 立即上报一次（后台线程） */
    public void triggerOnce() {
        if (!started.get() || handler == null) return;
        handler.post(() -> {
            if (mqtt.isConnected()) {
                try { mqtt.publish(topic, UnifiedRobotState.i().toJson(), qos, retain); } catch (Throwable ignore) {}
            }
        });
    }

    /** 停止并释放线程 */
    public void stop() {
        if (!started.compareAndSet(true, false)) return;
        if (handler != null) handler.removeCallbacksAndMessages(null);
        if (worker != null) {
            worker.quitSafely();
            worker = null;
        }
        handler = null;
    }
}
