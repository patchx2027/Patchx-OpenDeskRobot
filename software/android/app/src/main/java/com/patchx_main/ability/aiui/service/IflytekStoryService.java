package com.patchx_main.ability.aiui.service;

import android.content.Context;
import android.util.Log;

import com.google.gson.JsonArray;
import com.google.gson.reflect.TypeToken;
import com.patchx_main.ability.aiui.base.BaseService;
import com.patchx_main.ability.aiui.base.SemanticNormal;
import com.patchx_main.ability.aiui.base.ServiceType;
import com.patchx_main.ability.aiui.bean.NlpResult;
import com.patchx_main.ability.aiui.bean.StoryBean;
import com.patchx_main.ability.tts.PlayType;
import com.patchx_main.ability.tts.PlayerManager;

import java.lang.reflect.Type;
import java.util.ArrayList;
import java.util.List;

public class IflytekStoryService extends BaseService<JsonArray> {
    private static final String TAG = "IflytekStoryService";

    public IflytekStoryService(Context context) {
        super(context);
    }

    @Override
    public ServiceType getType() {
        return ServiceType.story;
    }

    private List<StoryBean> storyList = new ArrayList<>();

    @Override
    public void handlerNlp(JsonArray data, String answer, JsonArray resultArray, NlpResult nlpResult) {
        List<SemanticNormal> semanticList = gson.fromJson(data.toString(), new TypeToken<List<SemanticNormal>>() {
        }.getType());
        if (semanticList != null && semanticList.size() > 0) {
            SemanticNormal semanticNormal = semanticList.get(0);
            String intent = semanticNormal.getIntent();

            if (intent.equals("NEXT") || intent.equals("PREVIOUS")) {
                if (storyList.size() > 0) {
                    StoryBean storyBean = storyList.get((int) (storyList.size() * Math.random()));
                    playRandom(storyBean, nlpResult);
                }
            } else if (intent.equals("RANDOM_QUERY")) {
                String s = resultArray.toString();
                Type type = new TypeToken<List<StoryBean>>() {
                }.getType();
                List<StoryBean> storyBeanList = gson.fromJson(s, type);
                if (storyBeanList != null && storyBeanList.size() > 0) {
                    storyList.clear();
                    storyList.addAll(storyBeanList);
                    StoryBean storyBean = storyBeanList.get(0);
                    Log.e(TAG, "handlerNlp: " + storyBean);
                    playRandom(storyBean, nlpResult);
                }
            }
        }
    }

    private void playRandom(StoryBean storyBean, NlpResult nlpResult) {
        nlpResult.needPlayTts = false;
        String playUrl = storyBean.getPlayUrl();
        PlayerManager instance = PlayerManager.getInstance();
        Log.e(TAG, "playRandom: " + storyBean);
        instance.play(PlayType.TTS, nlpResult.playContext, (state1, msg1) ->
                instance.play(PlayType.MUSIC, playUrl, (state, msg) -> Log.e(TAG, "onEnd: " + state + " " + msg)));

    }

    @Override
    public void handlerWake() {
        PlayerManager.getInstance().stop();
    }
}
