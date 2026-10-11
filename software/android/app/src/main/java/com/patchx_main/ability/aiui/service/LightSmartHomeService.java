package com.patchx_main.ability.aiui.service;

import android.content.Context;
import android.util.Log;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.reflect.TypeToken;
import com.patchx_main.ability.aiui.base.BaseService;
import com.patchx_main.ability.aiui.base.ServiceType;
import com.patchx_main.ability.aiui.bean.NlpResult;
import com.patchx_main.ability.aiui.bean.SmartHomeBean;

public class LightSmartHomeService extends BaseService<JsonObject> {
    private static final String TAG = "LightSmartHomeService";

    public LightSmartHomeService(Context mContext) {
        super(mContext);
    }

    @Override
    public ServiceType getType() {
        return ServiceType.story;
    }

    @Override
    public void handlerNlp(JsonObject data, String answer, JsonArray resultArray, NlpResult nlpResult) {
        JsonObject slots = data.getAsJsonObject("slots");
        SmartHomeBean smartHomeBean = gson.fromJson(slots, new TypeToken<SmartHomeBean>() {
        }.getType());
        Log.e(TAG, "handlerNlp: " + smartHomeBean);
    }
}
