package com.patchx_main.patchx.util;

import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import com.patchx_main.patchx.manage.DeviceManager;

import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

/**
 * InactivityScreenOffWatcher (单例版)
 * - i() 获取全局单例
 * - i().start() 开始，i().refresh() 刷新，i().stop() 停止
 */
public class InactivityScreenOffWatcher {

    private static final String TAG = "InactivityScreenOff";
    private static final long DEFAULT_TIMEOUT_MS = 2L * 60L * 1000L; // 2分钟

    // ============ 单例 ============
    private static final InactivityScreenOffWatcher INSTANCE =
            new InactivityScreenOffWatcher(DEFAULT_TIMEOUT_MS);

    public static InactivityScreenOffWatcher i() {
        return INSTANCE;
    }

    // ============ 实例字段 ============
    private final long timeoutMs;
    private final ScheduledExecutorService scheduler;
    private final Handler mainHandler;

    private final Object lock = new Object();
    private ScheduledFuture<?> pendingTask;
    private boolean started = false;

    // 私有构造，外部不能 new
    private InactivityScreenOffWatcher(long timeoutMs) {
        this.timeoutMs = Math.max(10_000L, timeoutMs);
        this.scheduler = Executors.newSingleThreadScheduledExecutor();
        this.mainHandler = new Handler(Looper.getMainLooper());
    }

    /** 开始监听 */
    public void start() {
        synchronized (lock) {
            if (started) return;
            started = true;
            scheduleNext();
            Log.d(TAG, "started, timeoutMs=" + timeoutMs);
        }
    }

    /** 停止 */
    public void stop() {
        synchronized (lock) {
            started = false;
            if (pendingTask != null) {
                pendingTask.cancel(true);
                pendingTask = null;
            }
        }
        Log.d(TAG, "stopped");
    }

    /** 刷新（有活动事件时调用） */
    public void refresh() {
        synchronized (lock) {
            if (!started) return;
            if (pendingTask != null) {
                pendingTask.cancel(false);
                pendingTask = null;
            }
            scheduleNext();
        }
    }

    /** 安排一次超时任务 */
    private void scheduleNext() {
        pendingTask = scheduler.schedule(this::onTimeoutFired,
                timeoutMs, TimeUnit.MILLISECONDS);
    }

    /** 超时触发：息屏 */
    private void onTimeoutFired() {
        synchronized (lock) {
            if (!started) return;
            pendingTask = null;
        }
        Log.d(TAG, "inactivity timeout reached, turning screen OFF");
        mainHandler.post(() -> {
            try {
                DeviceManager.getInstance().setLcdBackLightEnable(true);
            } catch (Throwable t) {
                Log.e(TAG, "turn screen off failed: " + t.getMessage(), t);
            }
        });
    }
}
