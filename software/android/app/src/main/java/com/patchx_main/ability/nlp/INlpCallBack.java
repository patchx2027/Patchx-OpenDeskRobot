package com.patchx_main.ability.nlp;

import com.patchx_main.ability.aiui.bean.NlpResult;

public interface INlpCallBack {
    void onState(boolean state, String msg);

    void onNlpResult(NlpResult nlp, boolean state);
}
