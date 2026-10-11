package com.patchx_main.patchx.voice;

import android.os.Handler;
import android.os.Looper;

/**
 * 通用循环定时器工具类
 * 支持立即执行/延迟执行、重置、停止
 */
public class TimerUtil {

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final long intervalMillis;
    private final TimerCallback callback;

    private Runnable timerRunnable;
    private boolean isRunning = false;

    /** 定时器回调接口 */
    public interface TimerCallback {
        void onTimerTick();
    }

    /**
     * 构造函数
     * @param intervalMillis 循环间隔时间（毫秒）
     * @param callback       定时回调
     */
    public TimerUtil(long intervalMillis, TimerCallback callback) {
        this.intervalMillis = intervalMillis;
        this.callback = callback;
    }

    /**
     * 启动定时器（默认延迟一个周期后执行第一次回调）
     */
    public void start() {
        start(false);
    }

    /**
     * 启动定时器
     * @param triggerImmediately 是否立即执行第一次回调
     */
    public void start(boolean triggerImmediately) {
        if (isRunning) return;
        isRunning = true;

        timerRunnable = new Runnable() {
            @Override
            public void run() {
                if (!isRunning) return;

                if (callback != null) {
                    try {
                        callback.onTimerTick();
                    } catch (Exception e) {
                        e.printStackTrace();
                    }
                }

                if (isRunning) {
                    handler.postDelayed(this, intervalMillis);
                }
            }
        };

        if (triggerImmediately) {
            handler.post(timerRunnable);           // 立即执行一次
        } else {
            handler.postDelayed(timerRunnable, intervalMillis); // 延迟一个周期执行
        }
    }

    /**
     * 停止定时器
     */
    public void stop() {
        if (!isRunning) return;
        isRunning = false;
        if (timerRunnable != null) {
            handler.removeCallbacks(timerRunnable);
        }
    }

    /**
     * 重置定时器（停止后重新启动）
     * @param triggerImmediately 是否立即执行第一次回调
     */
    public void reset(boolean triggerImmediately) {
        stop();
        start(triggerImmediately);
    }

    /**
     * 检查定时器是否运行中
     */
    public boolean isRunning() {
        return isRunning;
    }
}
