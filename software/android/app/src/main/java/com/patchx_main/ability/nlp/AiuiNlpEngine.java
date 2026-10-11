package com.patchx_main.ability.nlp;

import android.content.Context;
import android.text.TextUtils;
import android.util.Log;

import com.iflytek.aiui.AIUIAgent;
import com.iflytek.aiui.AIUIConstant;
import com.iflytek.aiui.AIUIEvent;
import com.patchx_main.ability.aiui.AIUIServiceManager;
import com.patchx_main.ability.aiui.bean.NlpResult;

import org.json.JSONException;
import org.json.JSONObject;

import java.nio.charset.Charset;

public class AiuiNlpEngine extends BaseNlpEngine {
    private static final String TAG = "AiuiNlpEngine";
    private AIUIAgent mAiuiAgent;
    private AIUIServiceManager manager;

    public AiuiNlpEngine(AIUIAgent aiuiAgent, Context context) {
        this.mAiuiAgent = aiuiAgent;
        manager = new AIUIServiceManager(context);
    }

    @Override
    public void init() {

    }

    @Override
    public void requestNlp(String asr) {

    }

    @Override
    public void cancel() {

    }

    @Override
    public void onEvent(Object tmp) {
        AIUIEvent event = (AIUIEvent) tmp;
        switch (event.eventType) {
            case AIUIConstant.EVENT_RESULT: {
                JSONObject bizParamJson = null;
                try {
                    bizParamJson = new JSONObject(event.info);
                    JSONObject data = bizParamJson.getJSONArray("data").getJSONObject(0);
                    JSONObject params = data.getJSONObject("params");
                    JSONObject content = data.getJSONArray("content").getJSONObject(0);
                    if (content.has("cnt_id")) {
                        String cntId = content.getString("cnt_id");
                        String cntStr = new String(event.data.getByteArray(cntId), Charset.forName("utf-8"));
                        if (TextUtils.isEmpty(cntStr)) {
                            Log.d(TAG, "onEvent:  cntStr is null ");
                            return;
                        }
                        String sub = params.optString("sub");
                        if ("nlp".equals(sub)) {
                            // 解析得到语义结果
                            JSONObject cntJson = new JSONObject(cntStr);
                            String result = cntJson.optString("intent");
                            // Payload logging omitted from the public build.
                            if (!"{}".equals(result)) {
                                NlpResult s = manager.parseResult(result);
                                callback(s, true);
                            }
                        }
                    }

                } catch (JSONException e) {
                    e.printStackTrace();
                }
                break;
            }
        }
    }
}
