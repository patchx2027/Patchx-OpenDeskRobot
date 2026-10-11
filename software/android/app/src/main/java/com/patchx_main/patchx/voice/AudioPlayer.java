package com.patchx_main.patchx.voice;

import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioTrack;
import android.os.SystemClock;
import android.util.Log;

import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * 单例流式音频播放器
 * 用于播放下行PCM音频（WebSocket接收）并提供回声消除参考
 *
 * 变更点（向后兼容）：
 * 1) 新增“空闲看门狗”，当超过 IDLE_TIMEOUT_MS 未写入音频时自动 stop（不影响现有调用）。
 * 2) 记录最后写入时间 lastWriteAt，便于判断空闲。
 * 3) 新增 markStreamEnd() 供业务在流结束处显式收尾（可选用）。
 */
public class AudioPlayer {
    private static final String TAG = "AudioPlayer";

    /** 是否启用自动空闲收尾（如不想要，置 false 即可，完全不影响主逻辑） */
    private static final boolean AUTO_IDLE_STOP_ENABLED = true;
    /** 连续多久未写入就认为“流结束”，自动 stop（可按场景调大/调小） */
    private static final long IDLE_TIMEOUT_MS = 200L;
    /** 看门狗轮询周期 */
    private static final long WATCHER_POLL_MS = 50L;

    private static AudioPlayer instance;
    private AudioTrack audioTrack;
    private boolean isPlaying = false;

    /** 最近一次成功写入时间（毫秒，单调时钟） */
    private volatile long lastWriteAt = 0L;

    /** 看门狗线程（单线程定时器） */
    private final ScheduledExecutorService idleWatcher = Executors.newSingleThreadScheduledExecutor();

    private final Object lock = new Object();

    private AudioPlayer() {
        initAudioTrack();
        startIdleWatcher(); // 不影响业务；AUTO_IDLE_STOP_ENABLED=false 时逻辑几乎不触发
    }

    /** 获取全局单例 */
    public static synchronized AudioPlayer getInstance() {
        if (instance == null) {
            instance = new AudioPlayer();
        }
        return instance;
    }

    /** 获取底层AudioTrack，用于回声消除参考时间戳 */
    public AudioTrack getAudioTrack() {
        return audioTrack;
    }

    /** 可选：外部查询是否处于播放态 */
    public boolean isPlaying() {
        return isPlaying;
    }

    /** 初始化AudioTrack */
    private void initAudioTrack() {
        try {
            AudioAttributes attributes = new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                    .build();

            AudioFormat format = new AudioFormat.Builder()
                    .setSampleRate(AudioConfig.SAMPLE_RATE)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .build();

            int minBufferSize = AudioTrack.getMinBufferSize(
                    AudioConfig.SAMPLE_RATE,
                    AudioFormat.CHANNEL_OUT_MONO,
                    AudioFormat.ENCODING_PCM_16BIT
            );
            int bufferSize = Math.max(minBufferSize * 4, 8192);

            audioTrack = new AudioTrack(
                    attributes,
                    format,
                    bufferSize,
                    AudioTrack.MODE_STREAM,
                    AudioManager.AUDIO_SESSION_ID_GENERATE
            );

            if (audioTrack.getState() != AudioTrack.STATE_INITIALIZED) {
                Log.e(TAG, "测试=====AudioTrack初始化失败");
            }
        } catch (Exception e) {
            Log.e(TAG, "测试=====初始化AudioTrack失败: " + e.getMessage());
        }
    }

    /** 播放一帧音频（线程安全） */
    public void playAudio(short[] pcmData) {
        if (pcmData == null || pcmData.length == 0) return;
        synchronized (lock) {
            if (audioTrack == null || audioTrack.getState() != AudioTrack.STATE_INITIALIZED) {
                initAudioTrack();
                if (audioTrack == null || audioTrack.getState() != AudioTrack.STATE_INITIALIZED) return;
            }

            if (!isPlaying) {
                try {
                    audioTrack.play();
                    isPlaying = true;
                } catch (IllegalStateException e) {
                    Log.w(TAG, "play() IllegalState, try re-init: " + e.getMessage());
                    // 保险：重建一次
                    try { audioTrack.release(); } catch (Exception ignore) {}
                    audioTrack = null;
                    initAudioTrack();
                    if (audioTrack == null || audioTrack.getState() != AudioTrack.STATE_INITIALIZED) return;
                    audioTrack.play();
                    isPlaying = true;
                }
            }

            int framesWritten = audioTrack.write(pcmData, 0, pcmData.length);
            if (framesWritten < 0) {
                Log.e(TAG, "测试=====AudioTrack写入失败，错误码: " + framesWritten);
            } else {
                lastWriteAt = SystemClock.elapsedRealtime(); // 成功写入才更新时间
            }
        }
    }

    /**
     * 显式标记“当前这段流结束了”（可选调用）。
     * 适用于你在收到服务器 END/FIN 帧时，主动让播放器收尾。
     */
    public void markStreamEnd() {
        // 给一点点时间让缓冲播完（无需严格，避免截断）
        drainAndStop(/*maxWaitMs*/ 800);
    }

    /** 停止播放并清空缓冲区（外部可用） */
    public void stopPlayback() {
        synchronized (lock) {
            if (audioTrack != null && isPlaying) {
                try {
                    audioTrack.pause();
                    audioTrack.flush();
                    audioTrack.stop();
                } catch (Exception e) {
                    Log.w(TAG, "测试=====停止播放异常: " + e.getMessage());
                } finally {
                    isPlaying = false;
                }
            }
        }
    }

    /** 释放播放器资源 */
    public void release() {
        try { stopPlayback(); } catch (Exception ignore) {}
        synchronized (lock) {
            if (audioTrack != null) {
                try {
                    audioTrack.release();
                } catch (Exception e) {
                    Log.w(TAG, "测试=====释放AudioTrack异常: " + e.getMessage());
                } finally {
                    audioTrack = null;
                    instance = null;
                }
            }
        }
        try { idleWatcher.shutdownNow(); } catch (Exception ignore) {}
    }

    /* ==================== 内部：看门狗与柔性收尾 ==================== */

    private void startIdleWatcher() {
        idleWatcher.scheduleAtFixedRate(() -> {
            if (!AUTO_IDLE_STOP_ENABLED) return;
            try {
                // 快速路径判定：没在播或没轨道，直接返回
                if (!isPlaying || audioTrack == null) return;

                long now = SystemClock.elapsedRealtime();
                long idleFor = now - lastWriteAt;
                if (idleFor < IDLE_TIMEOUT_MS) return; // 还不到超时

                // 进一步确认播放头是否在前进（避免误判）
                int pos1 = safeGetPlaybackHead();
                SystemClock.sleep(30);
                int pos2 = safeGetPlaybackHead();

                if (pos2 == pos1) {
                    // 基本可以确定缓冲已空/卡住，做一次柔性收尾
                    drainAndStop(/*maxWaitMs*/ 500);
                }
            } catch (Throwable t) {
                Log.w(TAG, "idleWatcher err: " + t.getMessage());
            }
        }, WATCHER_POLL_MS, WATCHER_POLL_MS, TimeUnit.MILLISECONDS);
    }

    /** 获取播放头位置（防止偶发 NPE/状态异常） */
    private int safeGetPlaybackHead() {
        try {
            AudioTrack at = audioTrack;
            if (at == null) return 0;
            return at.getPlaybackHeadPosition();
        } catch (Throwable t) {
            return 0;
        }
    }

    /**
     * 等待播放头前进（或到达上限），再 stop。
     * maxWaitMs：最大等待时长，避免阻塞过久。
     */
    private void drainAndStop(long maxWaitMs) {
        try {
            AudioTrack at;
            synchronized (lock) {
                at = audioTrack;
            }
            if (at == null) {
                stopPlayback();
                return;
            }

            long start = SystemClock.elapsedRealtime();
            int last = safeGetPlaybackHead();

            while (SystemClock.elapsedRealtime() - start < Math.max(0, maxWaitMs)) {
                int now = safeGetPlaybackHead();
                if (now == last) break;     // 不再前进，基本播空
                last = now;
                SystemClock.sleep(10);
            }
        } catch (Throwable ignore) {
        } finally {
            // 无论如何都停一次，确保状态复位为“可再次播放”
            stopPlayback();
        }
    }
}
