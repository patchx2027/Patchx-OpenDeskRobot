package com.patchx_main.ability.aiui.service;

import android.content.Context;
import android.util.Log;

import com.google.gson.JsonArray;
import com.google.gson.reflect.TypeToken;
import com.patchx_main.ability.aiui.base.BaseService;
import com.patchx_main.ability.aiui.base.SemanticNormal;
import com.patchx_main.ability.aiui.base.ServiceType;
import com.patchx_main.ability.aiui.bean.NewsBean;
import com.patchx_main.ability.aiui.bean.NlpResult;
import com.patchx_main.ability.tts.PlayType;
import com.patchx_main.ability.tts.PlayerManager;

import java.lang.reflect.Type;
import java.util.ArrayList;
import java.util.List;

public class IflytekNewsService extends BaseService<JsonArray> {
    private static final String TAG = "IflytekNewsService";
    private List<NewsBean> newsBeanList = new ArrayList<>();
    private int index = 0;

    public IflytekNewsService(Context mContext) {
        super(mContext);
    }

    @Override
    public ServiceType getType() {
        return ServiceType.news;
    }

    @Override
    public void handlerNlp(JsonArray semanticJson, String answer, JsonArray resultArray, NlpResult nlpResult) {
        List<SemanticNormal> semanticList = gson.fromJson(semanticJson.toString(), new TypeToken<List<SemanticNormal>>() {
        }.getType());
        if (semanticList != null && semanticList.size() > 0) {
            SemanticNormal semanticNormal = semanticList.get(0);
            String intent = semanticNormal.getIntent();

            Log.e(TAG, "handlerNlp: " + intent);
            if (intent.equals("NEXT") || intent.equals("PREVIOUS")) {
                if (newsBeanList.size() > 0) {
                    playNews();
                    answer = "好的";
                }
            } else if (intent.equals("PLAY")) {
                String s = resultArray.toString();
                Type type = new TypeToken<List<NewsBean>>() {
                }.getType();
                List<NewsBean> storyBeanList = gson.fromJson(s, type);
                if (storyBeanList != null && storyBeanList.size() > 0) {
                    newsBeanList.clear();
                    newsBeanList.addAll(storyBeanList);
                    playNews();
                }
            } else if (intent.equals("INSTRUCTION")) {
                List<SemanticNormal.Slots> slotsList = semanticNormal.getSlots();
                if (slotsList != null && slotsList.size() > 0) {
                    SemanticNormal.Slots slots = slotsList.get(0);
                    String value = slots.getValue();
                    Log.e(TAG, "handlerNlp: " + value);
                    if ("PREVIOUS".equals(value) || "NEXT".equals(value)) {
                        playNews();
                        answer = "好的";
                    } else if ("PAUSE".equals(value) || "CLOSE".equals(value)) {
                        PlayerManager.getInstance().stop();
                    }
                }
            }
        }
    }

    private void playNews() {
        if (index >= 0 && newsBeanList.size() > 0) {
            NewsBean newsBean = newsBeanList.get(index++ % newsBeanList.size());
            PlayerManager.getInstance().play(PlayType.MUSIC, newsBean.getUrl(), (state, msg) -> {
                playNews();
            });
        }
    }

    @Override
    public void handlerWake() {
        index = 0;
        PlayerManager.getInstance().stop();
    }
}
