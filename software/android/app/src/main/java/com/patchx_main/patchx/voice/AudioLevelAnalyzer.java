package com.patchx_main.patchx.voice;

import android.util.Log;

import org.greenrobot.eventbus.EventBus;
import com.patchx_main.patchx.event.AudioLevelEvent;
import com.patchx_main.patchx.unity.EmoteScheduler;

import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 音频电平分析器（固定每 200ms 输出；无流仅发一次 0 然后停）
 * - 有流：每 200ms 输出 1~6
 * - 刚停：超过 QUIET_GATE_MS 且未到 2s 的这段时间【不输出任何值】
 * - 无流：超过 2s 只发送一次 0，然后停止；下次 feed() 自动恢复
 */
public class AudioLevelAnalyzer {

    private static final String TAG = "AudioLevelAnalyzer";

    /** 固定输出间隔 */
    private static final long INTERVAL_MS   = 500;
    /** “刚停”静默门限（这段时间内不输出） */
    private static final long QUIET_GATE_MS = 400;
    /** 判定无流的超时时长（达到后仅发一次 0） */
    private static final long INACTIVITY_MS = 2000;

    /** EMA 参数（上升快、下降慢） */
    private static final float EMA_UP_ALPHA_RMS    = 0.60f;
    private static final float EMA_DOWN_ALPHA_RMS  = 0.20f;
    private static final float EMA_UP_ALPHA_PEAK   = 0.70f;
    private static final float EMA_DOWN_ALPHA_PEAK = 0.25f;
    private static final float SOFT_K   = 2.0f;  // 原 2.2，轻微减小压缩力度
    private static final float PRE_GAIN = 1.10f; // 原 1.20，降低一点前增益
    private static final float PEAK_MIX = 0.30f; // 原 0.38，降低瞬态权重

    // 5 档分界（压缩后 0~1 空间）——偏向 2/3/4
    private static final float L2 = 0.18f;
    private static final float L3 = 0.36f;
    private static final float L4 = 0.56f;
    // 运行状态
    private volatile ScheduledExecutorService scheduler;
    private final AtomicBoolean running = new AtomicBoolean(false);

    private volatile float emaRms  = 0f;
    private volatile float emaPeak = 0f;
    private volatile long  lastFeedNs = 0L;

    // 控制“无流只发一次 0”
    private volatile boolean zeroSentForThisSilence = false;


    private final EmoteScheduler emoteScheduler =
            new EmoteScheduler("PhotoManager", "OnPatchMessage");

    /** 解码后喂入 PCM（单声道 16k short[]） */
    public void feed(short[] pcm) {
        if (pcm == null || pcm.length == 0) return;

        float rms  = calcRms(pcm, pcm.length);
        float peak = calcPeakNorm(pcm, pcm.length);

        float aRms = (rms >= emaRms) ? EMA_UP_ALPHA_RMS : EMA_DOWN_ALPHA_RMS;
        emaRms  = aRms * rms  + (1f - aRms)  * emaRms;

        float aPk  = (peak >= emaPeak) ? EMA_UP_ALPHA_PEAK : EMA_DOWN_ALPHA_PEAK;
        emaPeak = aPk  * peak + (1f - aPk) * emaPeak;

        lastFeedNs = System.nanoTime();

        // 来了新流：清除“已发 0”标记，并确保调度在跑
        zeroSentForThisSilence = false;
        ensureRunning();
    }

    /** 固定 200ms 的输出任务 */
    private void tick() {
        long nowNs  = System.nanoTime();
        long idleMs = (lastFeedNs == 0L)
                ? INACTIVITY_MS + 1
                : TimeUnit.NANOSECONDS.toMillis(nowNs - lastFeedNs);

        if (idleMs > INACTIVITY_MS) {
            // 无流≥2s：只发一次 0，然后停掉调度，等下次 feed() 再启动
            if (!zeroSentForThisSilence) {
                zeroSentForThisSilence = true;
                safeEmitLevel(0);
            }
            stopInternal();
            return;
        }

        if (idleMs >= QUIET_GATE_MS) {
            // 刚停但未到 2s：什么都不发（避免刷 1/2）
            return;
        }

        // 有流：固定 200ms 输出一次 1~6
        float lin = (1f - PEAK_MIX) * emaRms + PEAK_MIX * emaPeak;
        float boosted = clamp01(lin * PRE_GAIN);
        float x = softCompress(boosted, SOFT_K);
        int level = mapLevel(x);
        safeEmitLevel(level);
    }

    /** RMS(0~1) */
    private float calcRms(short[] pcm, int len) {
        long sumSq = 0;
        for (int i = 0; i < len; i++) {
            int s = pcm[i];
            sumSq += (long) s * (long) s;
        }
        double meanSq = (sumSq / (double) len) / (32768.0 * 32768.0);
        if (meanSq <= 0.0) return 0f;
        return (float) Math.sqrt(meanSq);
    }

    /** 峰值(0~1) */
    private float calcPeakNorm(short[] pcm, int len) {
        int peak = 0;
        for (int i = 0; i < len; i++) {
            int v = Math.abs(pcm[i]);
            if (v > peak) peak = v;
        }
        return peak / 32768f;
    }

    /** 软压缩：y = ln(1 + k*x) / ln(1 + k) */
    private float softCompress(float v, float k) {
        if (v <= 0f) return 0f;
        if (v >= 1f) return 1f;
        double a = Math.log(1.0 + k * v);
        double b = Math.log(1.0 + k);
        return (float) (a / b);
    }

    private static float clamp01(float v) {
        return (v < 0f) ? 0f : (v > 1f ? 1f : v);
    }

    // 0~1 → 等级 1~5（主落 2/3/4，1/5 偶尔出现）
    private int mapLevel(float x) {
        if (x < 0.08f) return 1;      // 很低才给 1
        else if (x < L2) return 2;    // 0.08 ~ 0.18
        else if (x < L3) return 3;    // 0.18 ~ 0.36  ★主落
        else if (x < L4) return 4;    // 0.36 ~ 0.56  ★主落
        else return 5;                // 0.56+
    }

    /** 每次 tick 输出一条（固定 200ms） */
    private void safeEmitLevel(int level) {
        try {
            long ts = System.currentTimeMillis();
            emoteScheduler.sendMouthEmote(String.valueOf(level));
            EventBus.getDefault().post(new AudioLevelEvent(level));
            Log.d(TAG, "电平输出=" + level + " @ " + ts +
                    " (emaRms=" + emaRms + ", emaPeak=" + emaPeak + ")");
        } catch (Throwable t) {
            Log.e(TAG, "emitLevel error: " + t.getMessage());
        }
    }

    /** 固定频率调度（200ms） */
    private void ensureRunning() {
        if (running.get()) return;
        synchronized (this) {
            if (running.get()) return;
            scheduler = Executors.newSingleThreadScheduledExecutor();
            running.set(true);
            scheduler.scheduleAtFixedRate(this::tick, 0, INTERVAL_MS, TimeUnit.MILLISECONDS);
        }
    }

    /** 内部停止（无流触发） */
    private void stopInternal() {
        synchronized (this) {
            if (!running.get()) return;
            running.set(false);
            try { if (scheduler != null) scheduler.shutdownNow(); } catch (Throwable ignored) {}
            scheduler = null;
            // 保留 emaRms/emaPeak，恢复更平滑；zeroSentForThisSilence 在下一次 feed() 会被清
        }
    }

    /** 主动释放（生命周期结束时可调用） */
    public void release() {
        stopInternal();
        // 完全复位（可选）
        emaRms = 0f;
        emaPeak = 0f;
        lastFeedNs = 0L;
        zeroSentForThisSilence = false;
    }
}
