package com.patchx_main.patchx.voice;

import android.util.Log;


import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;

/**
 * 带时间戳的音频帧队列，支持按时间戳匹配取帧（用于对齐回声消除的参考数据）
 * 优化：
 * 1. 使用预创建静音帧，减少GC
 * 2. 队列满丢弃旧帧，保证低延迟
 * 3. 无匹配帧时优先使用最近帧兜底
 */
public class AudioFrameQueue {
    private static final String TAG = "AudioFrameQueue";
    private static final int MAX_QUEUE_SIZE = 8; // 队列最大容量（减少延迟累积）
    private static final long MAX_MATCH_DIFF_MS = 90; // 最大可接受时间差

    private final BlockingQueue<TimedAudioFrame> queue;
    private TimedAudioFrame lastFrame; // 缓存最近一帧，用于兜底
    private final short[] silenceFrame; // 预创建静音帧，减少GC

    public AudioFrameQueue() {
        queue = new ArrayBlockingQueue<>(MAX_QUEUE_SIZE);
        silenceFrame = new short[AudioConfig.FRAME_SIZE];
    }

    /**
     * 存入带播放时间戳的音频帧（WebSocket线程调用）
     * @param frame 音频帧数据
     * @param playbackTimestamp 该帧实际被播放的时间戳（毫秒）
     */
    public void putFrame(short[] frame, long playbackTimestamp) {
        if (frame == null) return;

        try {
            // 存入副本+时间戳，避免原数组被修改
            TimedAudioFrame timedFrame = new TimedAudioFrame(frame.clone(), playbackTimestamp);

            // 队列满时移除最旧帧（避免阻塞，优先保证最新数据）
            while (!queue.offer(timedFrame)) {
                queue.poll(); // 丢弃旧帧
            }
            lastFrame = timedFrame; // 更新最近帧缓存
        } catch (Exception e) {
            Log.w(TAG, "测试=====存入音频帧失败: " + e.getMessage());
        }
    }

    /**
     * 根据目标时间戳取最匹配的音频帧（录音线程调用）
     * @param targetTimestamp 目标匹配时间戳（毫秒）
     * @param frameSize 预期帧大小
     * @return 最匹配的音频帧（若无匹配则返回最近帧或静音帧）
     */
    public short[] takeMatchingFrame(long targetTimestamp, int frameSize) {
        try {
            TimedAudioFrame bestFrame = null;
            long minTimeDiff = Long.MAX_VALUE;

            // 复制队列快照进行遍历（避免阻塞）
            TimedAudioFrame[] frames = queue.toArray(new TimedAudioFrame[0]);
            for (TimedAudioFrame frame : frames) {
                long timeDiff = Math.abs(frame.playbackTimestamp - targetTimestamp);
                if (timeDiff < minTimeDiff) {
                    minTimeDiff = timeDiff;
                    bestFrame = frame;
                }
            }

            // 找到匹配帧且时间差在可接受范围内
            if (bestFrame != null && minTimeDiff < MAX_MATCH_DIFF_MS) {
                return bestFrame.frame;
            }
        } catch (Exception e) {
            Log.w(TAG, "测试=====取匹配帧失败: " + e.getMessage());
        }

        // ---------------- 兜底逻辑 ----------------
        if (lastFrame != null && lastFrame.frame.length == frameSize) {
            return lastFrame.frame.clone(); // 最近帧兜底
        } else {
            return silenceFrame; // 静音帧兜底
        }
    }

    /** 清空队列并释放资源 */
    public void clear() {
        queue.clear();
        lastFrame = null;
    }
}
