package com.patchx_main.ability.nlp;

import com.patchx_main.ability.aiui.bean.NlpResult;

public abstract class BaseNlpEngine {
    protected INlpCallBack mNlpCallBack;

    public void setNlpCallBack(INlpCallBack mNlpCallBack) {
        this.mNlpCallBack = mNlpCallBack;
    }

    protected void callback(NlpResult nlp, boolean state) {
        if (mNlpCallBack != null) {
            mNlpCallBack.onNlpResult(nlp, state);
        }
    }

    public abstract void init();

    public abstract void requestNlp(String asr);

    public abstract void cancel();

    public void onEvent(Object event) {
    }
}
