package com.patchx_main.ability.aiui.base;

import android.content.Context;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.patchx_main.ability.aiui.bean.NlpResult;

public abstract class BaseService<T> {
    protected Context mContext;

    public BaseService(Context mContext) {
        this.mContext = mContext;
    }

    protected Gson gson = new Gson();

    public abstract ServiceType getType();

    public abstract void handlerNlp(T semanticJson, String answer, JsonArray resultArray, NlpResult nlpResult);

    public boolean semanticArray() {
        return true;
    }

    public void handlerWake() {

    }
}
