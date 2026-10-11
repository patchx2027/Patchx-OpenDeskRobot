package com.patchx_main.caeLib.record;

import android.content.Context;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class SystemRecord extends BaseRecord {
    private static final String TAG = "SystemRecord";
    private AudioRecord mRecord;
    // 音频获取源
    private int audioSource = MediaRecorder.AudioSource.MIC;
    // 设置音频采样率，44100是目前的标准，但是某些设备仍然支持22050，16000，11025
    private static int sampleRateInHz = 16000;// 44100;
    // 设置音频的录制的声道CHANNEL_IN_STEREO为双声道，CHANNEL_CONFIGURATION_MONO为单声道
    private static int channelConfig = AudioFormat.CHANNEL_IN_STEREO;// AudioFormat.CHANNEL_IN_STEREO;
    // 音频数据格式:PCM 16位每个样本。保证设备支持。PCM 8位每个样本。不一定能得到设备支持。
    private static int audioFormat = AudioFormat.ENCODING_PCM_16BIT;
    // 音频大小
    private int minBufSize;
    private ExecutorService executors;
    private volatile boolean isStop = false;

    public SystemRecord(int sampleRate, int channel, int format) {
        sampleRateInHz = sampleRate;
        channelConfig = channel;
        audioFormat = format;
    }

    @Override
    public void init(Context context) {
        sample = sampleRateInHz;
        channel = channelConfig == AudioFormat.CHANNEL_CONFIGURATION_MONO ? 1 : 2;
        minBufSize = AudioRecord.getMinBufferSize(sampleRateInHz, channelConfig,
                audioFormat);
        mRecord = new AudioRecord(audioSource, sampleRateInHz, channelConfig,
                audioFormat, minBufSize);

        executors = Executors.newSingleThreadExecutor();
    }

    @Override
    public boolean isNeedCheck() {
        return false;
    }

    @Override
    public boolean startRecord() {
        if (mRecord != null) {
            int audioRecordState = mRecord.getState();
            if (audioRecordState != AudioRecord.STATE_INITIALIZED) {
                return false;
            }
            mRecord.startRecording();
            int recordingState = mRecord.getRecordingState();
            if (recordingState != AudioRecord.RECORDSTATE_RECORDING) {
                return false;
            }
            startReadData();
            return true;
        }
        return false;
    }

    private void startReadData() {
        executors.execute(() -> {
            byte[] audioData = new byte[minBufSize];
            int readSize;
            while (!isStop) {
                readSize = mRecord.read(audioData, 0, minBufSize);
                if (readSize > 0) {
                    onAudioCallBack(audioData, readSize);
                }
            }
            mRecord.stop();
        });
    }


    @Override
    public void stopRecord() {
        isStop = true;
    }
}
