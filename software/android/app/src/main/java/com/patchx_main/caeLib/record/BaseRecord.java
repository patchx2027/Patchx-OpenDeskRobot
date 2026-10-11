package com.patchx_main.caeLib.record;

import android.content.Context;
import android.util.Log;

public abstract class BaseRecord {
    private static final String TAG = "BaseRecord";
    private IAudioCallBack mAudioCallBack;
    protected int sample;
    protected int channel;

    public int getChannel() {
        return channel;
    }

    public int getSample() {
        return sample;
    }

    public void setAudioCallBack(IAudioCallBack mAudioCallBack) {
        this.mAudioCallBack = mAudioCallBack;
    }

    public boolean isNeedCheck() {
        return true;
    }

    protected void onAudioCallBack(byte[] data, int len) {
        if (mAudioCallBack != null) {
            mAudioCallBack.onAudio(data, len);
        }
    }

    public abstract void init(Context context);

    public abstract boolean startRecord();

    public abstract void stopRecord();

}
