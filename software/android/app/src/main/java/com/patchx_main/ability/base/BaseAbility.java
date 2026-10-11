package com.patchx_main.ability.base;

import android.content.Context;

import com.patchx_main.ability.asr.BaseAsrEngine;
import com.patchx_main.ability.nlp.BaseNlpEngine;
import com.patchx_main.ability.tts.BasePlayEngine;

public abstract class BaseAbility {
    protected BaseAsrEngine mAsrEngine;
    protected BasePlayEngine mTtsEngine;
    protected BaseNlpEngine mNlpEngine;

    public abstract void init(Context context, IInitCallBack iInitCallBack);

    public abstract BaseAsrEngine getAsrEngine();

    public abstract BaseNlpEngine getNlpEngine();

    public void onDestroy() {
    }
}
