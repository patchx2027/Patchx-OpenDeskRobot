package com.patchx_main.ability.tts;

import android.content.res.AssetFileDescriptor;

public abstract class BasePlayEngine {
    public static final int playEnd = 0;
    public static final int playError = 1;
    protected IPlayCallBack mTtsCallBack;

    protected void callBack(int state, String msg) {
        if (mTtsCallBack != null) {
            mTtsCallBack.onPlayEnd(state, msg);
            mTtsCallBack = null;
        }
    }

    public abstract void init();

    protected abstract void play(String text, IPlayCallBack iTtsCallBack);

    protected void play(AssetFileDescriptor assetFileDescriptor, IPlayCallBack iTtsCallBack) {
    }

    public abstract void stop();

    public abstract void release();

    public void onEvent(Object event) {
    }
}
