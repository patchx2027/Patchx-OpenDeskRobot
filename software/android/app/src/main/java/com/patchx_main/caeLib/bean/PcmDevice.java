package com.patchx_main.caeLib.bean;

public class PcmDevice {
    /**
     * pcm 声卡号 -D
     */
    private int mPcmCard = 0;

    /**
     * pcm 声卡设备号
     */
    private int mPcmDevice = 0;
    /**
     * 通道数量
     */
    private int mPcmChannel = 8;//6mic
    //private   int mPcmChannel = 4;//2mic
    /**
     * 采样率
     */
    private int mPcmSampleRate = 16000;
    /**
     * 一次中断的帧数 一般不同修改，某些不支持这么大数字时会报错，可以尝试减小改值，例如 1023
     */
    private final int mPcmPeriodSize = 1536;
    /**
     * 周期数 一般不同修改
     */
    private final int mPcmPeriodCount = 8;
    /**
     * pcm 位宽 0-PCM_FORMAT_S16_LE、<br>1-PCM_FORMAT_S32_LE、<br>2-PCM_FORMAT_S8、<br>3-PCM_FORMAT_S24_LE、<br>4-PCM_FORMAT_MAX
     */
    private int mPcmFormat = 0;

    public PcmDevice(int mPcmCard) {
        this.mPcmCard = mPcmCard;
    }

    public PcmDevice(int mPcmCard, int mPcmDevice, int mPcmChannel) {
        this.mPcmCard = mPcmCard;
        this.mPcmDevice = mPcmDevice;
        this.mPcmChannel = mPcmChannel;
    }

    public PcmDevice(int mPcmCard, int mPcmDevice, int mPcmChannel, int mPcmSampleRate) {
        this.mPcmCard = mPcmCard;
        this.mPcmDevice = mPcmDevice;
        this.mPcmChannel = mPcmChannel;
        this.mPcmSampleRate = mPcmSampleRate;
    }

    public int getmPcmCard() {
        return mPcmCard;
    }

    public int getmPcmDevice() {
        return mPcmDevice;
    }

    public int getmPcmChannel() {
        return mPcmChannel;
    }

    public int getmPcmSampleRate() {
        return mPcmSampleRate;
    }

    public int getmPcmPeriodSize() {
        return mPcmPeriodSize;
    }

    public int getmPcmPeriodCount() {
        return mPcmPeriodCount;
    }

    public int getmPcmFormat() {
        return mPcmFormat;
    }

    @Override
    public String toString() {
        return "PcmDevice{" +
                "mPcmCard=" + mPcmCard +
                ", mPcmDevice=" + mPcmDevice +
                ", mPcmChannel=" + mPcmChannel +
                ", mPcmSampleRate=" + mPcmSampleRate +
                ", mPcmPeriodSize=" + mPcmPeriodSize +
                ", mPcmPeriodCount=" + mPcmPeriodCount +
                ", mPcmFormat=" + mPcmFormat +
                '}';
    }
}
