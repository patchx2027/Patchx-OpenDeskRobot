package com.patchx_main.ability.asr;

public interface IAsrEngineCallBack {
    void onState(int state, String msg);

    void onAsrResult(String text, boolean isLast);
}
