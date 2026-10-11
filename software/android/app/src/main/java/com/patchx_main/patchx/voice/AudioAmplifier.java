package com.patchx_main.patchx.voice;

public class AudioAmplifier {
    // 16位PCM的最大振幅（绝对值）
    private static final int MAX_16BIT_AMPLITUDE = 32767;

    /**
     * 将音频放大到目标振幅（最大振幅的百分比）
     * @param pcmData 原始16位PCM数据
     * @param targetPercent 目标振幅占最大振幅的百分比（如80表示80%）
     * @return 放大后的PCM数据
     */
    public static short[] amplifyToTarget(short[] pcmData, int targetPercent) {
        if (pcmData == null || pcmData.length == 0) {
            return new short[0];
        }
        if (targetPercent <= 0 || targetPercent > 100) {
            throw new IllegalArgumentException("目标百分比必须在1~100之间");
        }

        // 1. 计算当前最大振幅
        short currentMax = getMaxAmplitude(pcmData);
        if (currentMax == 0) {
            return pcmData; // 静音，无需放大
        }

        // 2. 计算目标振幅（最大振幅的targetPercent%）
        int targetAmplitude = (int) (MAX_16BIT_AMPLITUDE * (targetPercent / 100.0f));

        // 3. 计算增益（限制最大增益为10倍，避免过度失真）
        float gain = (float) targetAmplitude / currentMax;
        gain = Math.min(gain, 10.0f); // 最大放大10倍

        // 4. 应用增益并限幅
        return amplifyAudio(pcmData, gain);
    }

    // 示例：计算16位PCM音频的最大振幅（short[] 类型）
    public static short getMaxAmplitude(short[] pcmData) {
        short max = 0;
        for (short sample : pcmData) {
            short absSample = (short) Math.abs(sample);
            if (absSample > max) {
                max = absSample;
            }
        }
        return max;
    }

    /**
     * 将音频放大到目标振幅均值（最大振幅的百分比）
     * @param pcmData 原始16位PCM数据
     * @param targetPercent 目标振幅均值占最大振幅的百分比（如80表示80%）
     * @return 放大后的PCM数据
     */
    public static short[] amplifyToTargetMean(short[] pcmData, int targetPercent) {
        if (pcmData == null || pcmData.length == 0) {
            return new short[0];
        }
        if (targetPercent <= 0 || targetPercent > 100) {
            throw new IllegalArgumentException("目标百分比必须在1~100之间");
        }

        // 1. 计算当前振幅均值（所有样本绝对值的平均值）
        float currentMean = getAmplitudeMean(pcmData);
        if (currentMean <= 0) {
            return pcmData; // 静音或接近静音，无需放大
        }

        // 2. 计算目标均值（最大振幅的targetPercent%）
        float targetMean = MAX_16BIT_AMPLITUDE * (targetPercent / 100.0f);

        // 3. 计算增益（限制最大增益为10倍，避免过度失真）
        float gain = targetMean / currentMean;
        gain = Math.min(gain, 10.0f); // 最大放大10倍

        // 4. 应用增益并限幅
        return amplifyAudio(pcmData, gain);
    }


    // 计算16位PCM音频的振幅均值（所有样本绝对值的平均值）
    public static float getAmplitudeMean(short[] pcmData) {
        if (pcmData == null || pcmData.length == 0) {
            return 0;
        }

        long sum = 0;
        for (short sample : pcmData) {
            sum += Math.abs(sample);
        }
        return (float) sum / pcmData.length;
    }

    // 示例：对16位PCM数据应用增益（short[]）
    public static short[] amplifyAudio(short[] pcmData, float gain) {
        short[] amplified = new short[pcmData.length];
        for (int i = 0; i < pcmData.length; i++) {
            // 放大采样点
            float amplifiedSample = pcmData[i] * gain;
            // 限幅：确保不超过16位PCM的范围（-32768 ~ 32767）
            amplifiedSample = Math.max(-32768, Math.min(amplifiedSample, 32767));
            amplified[i] = (short) amplifiedSample;
        }
        return amplified;
    }
}
