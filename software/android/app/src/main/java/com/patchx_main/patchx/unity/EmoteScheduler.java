package com.patchx_main.patchx.unity;

import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;

import com.patchx_main.patchx.manage.UnityManager;

import java.util.Random;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 表情随机发送调度器
 * - 有流时每 10s 随机发 001~005
 * - 无流时超过 12s 自动发一次 000 并停止
 */
public class EmoteScheduler {
    private static final String TAG = "EmoteScheduler";

    private static final long EMOTE_INTERVAL_MS = 10_000L;   // 有流心跳间隔
    private static final long IDLE_TIMEOUT_MS   = 12_000L;   // 判定无流超时

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final AtomicBoolean running = new AtomicBoolean(false);
    private final AtomicLong lastFrameAt = new AtomicLong(0);
    private final Random rnd = new Random();

    private final String unityGo;
    private final String unityMethod;

    public EmoteScheduler(String unityGo, String unityMethod) {
        this.unityGo = unityGo;
        this.unityMethod = unityMethod;
    }

    /** 收到一帧音频时调用（刷新活跃时间，必要时拉起调度） */
    public void onStreamFrame() {
        lastFrameAt.set(SystemClock.elapsedRealtime());
        startIfNeeded();
    }

    /** 强制停止并发待机 000 */
    public void stop() {
        if (running.compareAndSet(true, false)) {
            mainHandler.removeCallbacks(ticker);
            sendEmote("000"); // 切回待机
        }
    }

    // ==== 内部 ====
    private void startIfNeeded() {
        if (running.compareAndSet(false, true)) {
            mainHandler.postDelayed(ticker, EMOTE_INTERVAL_MS);
        }
    }

    private final Runnable ticker = new Runnable() {
        @Override public void run() {
            long now = SystemClock.elapsedRealtime();
            long last = lastFrameAt.get();

            // 无流：超时自动停机并发待机
            if (now - last >= IDLE_TIMEOUT_MS) {
                sendEmote("000");
                stop();
                return;
            }

            // 有流：随机 001~005
            String emote = "00" + (1 + rnd.nextInt(12));
            sendEmote(emote);

            // 下一次
            mainHandler.postDelayed(this, EMOTE_INTERVAL_MS);
        }
    };

    public void sendEmote(String code) {
        EmoteMessage msg = new EmoteMessage(1, code);
        String json = msg.toJson();
        UnityManager.getInstance().send(unityGo, unityMethod, json);
        Log.d(TAG, "测试=====发送表情系统===Emote -> Unity: " + json);
    }

    public void sendMouthEmote(String code) {
        EmoteMessage msg = new EmoteMessage(2, code);
        String json = msg.toJson();
        UnityManager.getInstance().send(unityGo, unityMethod, json);
        //Log.d(TAG, "测试=====发送嘴型系统===MouthEmote -> Unity: " + json);
    }
}
