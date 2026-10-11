package com.patchx_main.patchx.mqtt;

import android.os.Handler;
import android.os.HandlerThread;
import java.util.concurrent.atomic.AtomicBoolean;

public class FixedPayloadReporter {
    private final AliMqttClient mqtt;
    private final String topic;
    private final int qos;
    private final boolean retain;
    private final long periodMs;
    private final String payload;

    private final AtomicBoolean started = new AtomicBoolean(false);
    private HandlerThread worker;
    private Handler handler;

    private final Runnable task = new Runnable() {
        @Override public void run() {
            if (!started.get()) return;
            if (mqtt.isConnected()) {
                try {
                    mqtt.publish(topic, payload, qos, retain);
                } catch (Throwable ignore) {}
            }
            handler.postDelayed(this, periodMs);
        }
    };

    public FixedPayloadReporter(AliMqttClient mqtt, String topic, int qos, boolean retain, long periodMs, String payload) {
        this.mqtt = mqtt;
        this.topic = topic;
        this.qos = qos;
        this.retain = retain;
        this.periodMs = Math.max(5_000L, periodMs);
        this.payload = payload;
    }

    public void start() {
        if (!started.compareAndSet(false, true)) return;
        worker = new HandlerThread("FixedPayloadReporter");
        worker.start();
        handler = new Handler(worker.getLooper());
        handler.post(task);
    }

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
