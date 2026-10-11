package com.patchx_main.patchx.voice;



/**
 * 带时间戳的音频帧，用于对齐播放时间和麦克风采集时间
 */
public class TimedAudioFrame {
    public short[] frame; // 音频帧数据
    public long playbackTimestamp; // 该帧实际被播放的时间戳（毫秒）

    public TimedAudioFrame(short[] frame, long playbackTimestamp) {
        this.frame = frame;
        this.playbackTimestamp = playbackTimestamp;
    }
}
