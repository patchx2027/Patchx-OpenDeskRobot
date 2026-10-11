package com.patchx_main.patchx.voice;



import android.media.AudioFormat;
import com.patchx_main.BuildConfig;

public class AudioConfig {
    // 音频参数配置（与Python版本保持一致）
    private AudioConfig(){

    }
    public static final int SAMPLE_RATE = 16000; // 16kHz
    public static final int CHANNEL_CONFIG = AudioFormat.CHANNEL_IN_MONO; // 录音单声道
    public static final int PLAY_CHANNEL_CONFIG = AudioFormat.CHANNEL_OUT_MONO; // 播放单声道
    public static final int AUDIO_FORMAT = AudioFormat.ENCODING_PCM_16BIT; // 16位PCM
    public static final int FRAME_DURATION = 60; // 帧时长（毫秒）
    public static final int FRAME_SIZE = SAMPLE_RATE * FRAME_DURATION / 1000; // 每帧样本数：960
    public static final String CLIENT_ID = BuildConfig.PATCHX_CLIENT_ID;
}
