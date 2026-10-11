package com.patchx_main.ability;

import android.content.Context;

import com.patchx_main.ability.asr.BaseAsrEngine;
import com.patchx_main.ability.base.AiuiAbility;
import com.patchx_main.ability.base.BaseAbility;
import com.patchx_main.ability.base.IInitCallBack;
import com.patchx_main.ability.nlp.BaseNlpEngine;

public class AbilityManager {
    private static AbilityManager instance = new AbilityManager();

    public static AbilityManager getInstance() {
        return instance;
    }

    private AbilityManager() {

    }

    public void init(Context context, IInitCallBack iInitCallBack) {
        baseAbility = new AiuiAbility();
        baseAbility.init(context.getApplicationContext(), iInitCallBack);
    }

    public void onDestroy() {
        if (baseAbility != null) {
            baseAbility.onDestroy();
        }
    }

    private BaseAbility baseAbility;

    public BaseAsrEngine getAsrEngine() {
        return baseAbility.getAsrEngine();
    }

    public BaseNlpEngine getNlpEngine() {
        return baseAbility.getNlpEngine();
    }
}
