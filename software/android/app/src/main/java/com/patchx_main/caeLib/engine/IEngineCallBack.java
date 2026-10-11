package com.patchx_main.caeLib.engine;

import com.patchx_main.caeLib.bean.WakeInfo;

public interface IEngineCallBack {
    void onWakeup(WakeInfo wakeInfo);


    void onAudioCallback(byte[] bytes, int len);
}
