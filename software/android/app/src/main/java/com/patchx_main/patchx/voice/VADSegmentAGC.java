package com.patchx_main.patchx.voice;

import android.util.Log;

public class VADSegmentAGC {
    private static final String TAG = "VADSegmentAGC";
    // 16000Hz采样率下，20ms帧长对应的样本数：16000 * 0.02 = 320
    private static final int FRAME_SIZE = 320;
    private static final float FRAME_DURATION = 0.02f; // 帧时长（秒）

    // AGC核心参数（针对2秒语音片段优化）
    private static final float TARGET_LEVEL_DB = -16.0f; // 目标电平（语音场景稍高）
    private static final float MAX_GAIN_DB = 50.0f;      // 最大增益（允许更大放大）
    private static final float MIN_GAIN_DB = -12.0f;     // 最小增益
    private static final float ATTACK_TIME = 0.08f;      // 攻击时间（稍长，避免过度反应）
    private static final float RELEASE_TIME = 0.3f;      // 释放时间（更长，过渡更平滑）
    private static final float NOISE_GATE_THRESHOLD = -40.0f; // 噪声门阈值（更低，保留弱信号）

    private float currentGainDb = 0.0f; // 增益状态（跨帧保持）

    /**
     * 处理VAD检测后的完整PCM片段（约2秒）
     * @param vadPcm 输入的PCM数据（16000Hz，16位单声道）
     * @return 音量均衡后的PCM数据
     */
    public short[] processVADSegment(short[] vadPcm) {
        if (vadPcm == null || vadPcm.length == 0) {
            Log.w(TAG, "输入PCM数据为空");
            return vadPcm;
        }

        int totalSamples = vadPcm.length;
        short[] outputPcm = new short[totalSamples];
        int frameCount = (int) Math.ceil((double) totalSamples / FRAME_SIZE);

        // 逐帧处理
        for (int i = 0; i < frameCount; i++) {
            int start = i * FRAME_SIZE;
            int end = Math.min(start + FRAME_SIZE, totalSamples);
            int frameLength = end - start;

            // 提取当前帧
            short[] frame = new short[frameLength];
            System.arraycopy(vadPcm, start, frame, 0, frameLength);

            // 处理当前帧
            short[] processedFrame = processSingleFrame(frame);

            // 保存处理结果
            System.arraycopy(processedFrame, 0, outputPcm, start, frameLength);
        }

        // 全局微调：确保整体音量更接近目标（可选优化）
        outputPcm = fineTuneGlobalGain(outputPcm);

        return outputPcm;
    }

    /**
     * 处理单帧PCM数据
     */
    private short[] processSingleFrame(short[] frame) {
        // 1. 计算当前帧的RMS能量（归一化到0~1）
        float rms = calculateRMS(frame);
        if (rms < 1e-6) { // 静音帧直接返回
            return frame;
        }

        // 2. 转换为dBFS（相对于满量程）
        float inputLevelDb = 20.0f * (float) Math.log10(rms);

        // 3. 计算目标增益（结合噪声门）
        float desiredGainDb;
        if (inputLevelDb < NOISE_GATE_THRESHOLD) {
            // 噪声帧：不放大，甚至轻微衰减
            desiredGainDb = Math.min(currentGainDb, -3.0f);
        } else {
            // 有效信号：计算达到目标电平所需增益
            desiredGainDb = TARGET_LEVEL_DB - inputLevelDb;
        }

        // 4. 平滑调整增益（攻击/释放时间控制）
        if (desiredGainDb < currentGainDb) {
            // 音量过大：按攻击时间减小增益
            float maxDecrease = (currentGainDb - desiredGainDb) / ATTACK_TIME * FRAME_DURATION;
            currentGainDb = Math.max(desiredGainDb, currentGainDb - maxDecrease);
        } else {
            // 音量过小：按释放时间增大增益
            float maxIncrease = (desiredGainDb - currentGainDb) / RELEASE_TIME * FRAME_DURATION;
            currentGainDb = Math.min(desiredGainDb, currentGainDb + maxIncrease);
        }

        // 5. 限制增益范围
        currentGainDb = Math.max(MIN_GAIN_DB, Math.min(MAX_GAIN_DB, currentGainDb));

        // 6. 应用增益到当前帧
        float gainLinear = (float) Math.pow(10.0, currentGainDb / 20.0);
        short[] processedFrame = new short[frame.length];
        for (int i = 0; i < frame.length; i++) {
            // 限制输出在16位PCM范围内（-32768 ~ 32767）
            float value = frame[i] * gainLinear;
            processedFrame[i] = (short) Math.max(-32768, Math.min(32767, value));
        }

        return processedFrame;
    }

    /**
     * 计算PCM帧的均方根（RMS）
     */
    private float calculateRMS(short[] frame) {
        long sumOfSquares = 0;
        for (short sample : frame) {
            sumOfSquares += (long) sample * sample;
        }
        float meanSquare = (float) sumOfSquares / frame.length;
        // 归一化到0~1（16位PCM最大值为32767）
        return (float) Math.sqrt(meanSquare) / 32768.0f;
    }

    /**
     * 全局增益微调（可选步骤）
     * 确保整个片段的平均音量更接近目标电平
     */
    private short[] fineTuneGlobalGain(short[] pcm) {
        // 计算整体RMS
        float globalRms = calculateRMS(pcm);
        if (globalRms < 1e-6) {
            return pcm;
        }

        // 计算整体电平
        float globalLevelDb = 20.0f * (float) Math.log10(globalRms);
        // 计算微调增益（不超过±3dB，避免过度修正）
        float fineTuneGainDb = Math.max(-3.0f, Math.min(3.0f, TARGET_LEVEL_DB - globalLevelDb));
        float fineTuneLinear = (float) Math.pow(10.0, fineTuneGainDb / 20.0);

        // 应用微调
        short[] tunedPcm = new short[pcm.length];
        for (int i = 0; i < pcm.length; i++) {
            float value = pcm[i] * fineTuneLinear;
            tunedPcm[i] = (short) Math.max(-32768, Math.min(32767, value));
        }

        return tunedPcm;
    }
}
