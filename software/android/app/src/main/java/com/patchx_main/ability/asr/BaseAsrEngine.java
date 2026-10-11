package com.patchx_main.ability.asr;

public abstract class BaseAsrEngine {
    private IAsrEngineCallBack mAsrEngineCallBack;

    public void setAsrEngineCallBack(IAsrEngineCallBack mAsrEngineCallBack) {
        this.mAsrEngineCallBack = mAsrEngineCallBack;
    }

    protected void callBack(String text, boolean isLast) {
        if (mAsrEngineCallBack != null) {
            mAsrEngineCallBack.onAsrResult(text, isLast);
        }
    }

    protected void callBack(int state, String msg) {
        if (mAsrEngineCallBack != null) {
            mAsrEngineCallBack.onState(state, msg);
        }
    }

    public abstract void init();

    public abstract void writeAudio(byte[] data, int len);

    public abstract void startAsr();

    public abstract void wakeUp();

    public abstract void stopAsr();

    public abstract void cancelAsr();

    public void onEvent(Object event) {

    }
}
