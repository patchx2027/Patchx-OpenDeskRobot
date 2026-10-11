package com.patchx_main.ability.aiui;

import android.content.Context;
import android.text.TextUtils;
import android.util.Log;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.reflect.TypeToken;
import com.patchx_main.ability.aiui.base.BaseService;
import com.patchx_main.ability.aiui.base.NlpBaseBean;
import com.patchx_main.ability.aiui.base.ServiceType;
import com.patchx_main.ability.aiui.bean.NlpResult;
import com.patchx_main.ability.aiui.service.IflytekMusicProService;
import com.patchx_main.ability.aiui.service.IflytekNewsService;
import com.patchx_main.ability.aiui.service.IflytekStoryService;
import com.patchx_main.ability.aiui.service.LightSmartHomeService;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.HashMap;
import java.util.Map;
import java.util.Set;

public class AIUIServiceManager {
    private static final String TAG = "AiuiServiceManager";
    private final Map<String, BaseService> hashMap = new HashMap<>();

    public AIUIServiceManager(Context context) {
        IflytekMusicProService value = new IflytekMusicProService(context);
        hashMap.put(ServiceType.musicPro.name(), value);
        hashMap.put(ServiceType.musicX.name(), value);
        hashMap.put(ServiceType.story.name(), new IflytekStoryService(context));
        hashMap.put(ServiceType.light_smartHome.name(), new LightSmartHomeService(context));
        hashMap.put(ServiceType.news.name(), new IflytekNewsService(context));
    }

    private BaseService prevBaseService = null;

    public void handlerWake() {
        Set<String> strings = hashMap.keySet();
        for (String string : strings) {
            hashMap.get(string).handlerWake();
        }
    }

    public NlpResult parseResult(String string) {
        JSONObject intent = null;
        String answer = "";
        NlpResult nlpResult = new NlpResult();
        try {
            intent = new JSONObject(string);
            String service = intent.optString("service");
            JSONObject answerObj = intent.optJSONObject("answer");

            if (answerObj != null) {
                answer = answerObj.optString("text");
            }
            nlpResult.playContext = nlpResult.showContext = answer;
            // Payload logging omitted from the public build.
            BaseService baseService = hashMap.get(service);
            if (baseService != null) {
                prevBaseService = baseService;
                if (baseService.semanticArray()) {
                    NlpBaseBean<JsonArray> nlpBaseBean =
                            new Gson().fromJson(string, new TypeToken<NlpBaseBean<JsonArray>>() {
                            }.getType());
                    baseService.handlerNlp(nlpBaseBean.getSemantic(), answer, nlpBaseBean.getDataResult(), nlpResult);
                } else {
                    NlpBaseBean<JsonObject> nlpBaseBean =
                            new Gson().fromJson(string, new TypeToken<NlpBaseBean<JsonObject>>() {
                            }.getType());
                    baseService.handlerNlp(nlpBaseBean.getSemantic(), answer, nlpBaseBean.getDataResult(), nlpResult);
                }

            } else {

            }
        } catch (JSONException e) {
            e.printStackTrace();
        }
        return nlpResult;
    }

    private String answerHandler(String answer) {
        if (TextUtils.isEmpty(answer)) {
            answer = "";
        } else {
            //handlerWake();
        }
        return answer;
    }

    public void handleDestroy() {
        if (prevBaseService != null) {
            prevBaseService.handlerWake();
        }
    }
}
