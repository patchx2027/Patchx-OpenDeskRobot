package com.patchx_main.ability.asr;

import android.text.TextUtils;
import android.util.Log;

import com.iflytek.aiui.AIUIAgent;
import com.iflytek.aiui.AIUIConstant;
import com.iflytek.aiui.AIUIEvent;
import com.iflytek.aiui.AIUIMessage;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.nio.charset.Charset;
public class AiuiAsrEngine extends BaseAsrEngine {
    private static final String TAG = "AiuiAsrEngine";
    private AIUIAgent mAiuiAgent;
    private volatile int mAIUIState = AIUIConstant.STATE_IDLE;

    public AiuiAsrEngine(AIUIAgent aiuiAgent) {
        this.mAiuiAgent = aiuiAgent;
    }

    @Override
    public void init() {

    }

    @Override
    public void writeAudio(byte[] data, int len) {
        if (mAIUIState == AIUIConstant.STATE_WORKING) {
            //Log.e(TAG,mAIUIState+"送入音频事件");
            String params = "data_type=audio,sample_rate=16000";
            AIUIMessage msg = new AIUIMessage(AIUIConstant.CMD_WRITE, 0, 0, params, data);
            mAiuiAgent.sendMessage(msg);
        } else {
            //Log.e(TAG,"未送入音频： mAIUIState ="+mAIUIState+"  isAsring： "+isAsring);
        }
    }

    @Override
    public void startAsr() {
        Log.d(TAG, "startAsr: " + mAIUIState);
        if (mAIUIState != AIUIConstant.STATE_WORKING) {
            AIUIMessage resetWakeupMsg = new AIUIMessage(
                    AIUIConstant.CMD_WAKEUP, 0, 0, "", null);
            mAiuiAgent.sendMessage(resetWakeupMsg);
        }
    }

    @Override
    public void stopAsr() {
        Log.d(TAG, "stopAsr: ");
        AIUIMessage resetWakeupMsg = new AIUIMessage(
                AIUIConstant.CMD_RESET_WAKEUP, 0, 0, "", null);
        mAiuiAgent.sendMessage(resetWakeupMsg);
    }

    @Override
    public void wakeUp() {
        AIUIMessage resetWakeupMsg = new AIUIMessage(
                AIUIConstant.CMD_WAKEUP, 0, 0, "", null);
        mAiuiAgent.sendMessage(resetWakeupMsg);
    }

    @Override
    public void cancelAsr() {
        stopAsr();
    }

    private StringBuilder stringBuilder = new StringBuilder();

    @Override
    public void onEvent(Object event) {
        AIUIEvent aiuiEvent = (AIUIEvent) event;
        switch (aiuiEvent.eventType) {
            case AIUIConstant.EVENT_WAKEUP:
                break;
            case AIUIConstant.EVENT_SLEEP: {
                callBack(aiuiEvent.eventType, "");
            }
            break;
            case AIUIConstant.EVENT_STATE: {
                // 状态事件
                mAIUIState = aiuiEvent.arg1;
                Log.d(TAG, "EVENT_STATE " + mAIUIState);
            }
            break;
            case AIUIConstant.EVENT_VAD: {
                if (AIUIConstant.VAD_BOS == aiuiEvent.arg1) {
                    Log.d(TAG, "找到vad_bos");
                } else if (AIUIConstant.VAD_EOS == aiuiEvent.arg1) {
                    Log.d(TAG, "找到vad_eos");
                } else if (AIUIConstant.VAD_BOS_TIMEOUT == aiuiEvent.arg1) {
                    Log.d(TAG, "onEvent: VAD_BOS_TIMEOUT  ");
                }
            }
            break;
            case AIUIConstant.EVENT_RESULT: {
                JSONObject bizParamJson = null;
                try {
                    bizParamJson = new JSONObject(aiuiEvent.info);
                    JSONObject data = bizParamJson.getJSONArray("data").getJSONObject(0);
                    JSONObject params = data.getJSONObject("params");
                    JSONObject content = data.getJSONArray("content").getJSONObject(0);
                    if (content.has("cnt_id")) {
                        String cntId = content.getString("cnt_id");
                        String cntStr = new String(aiuiEvent.data.getByteArray(cntId), Charset.forName("utf-8"));
                        if (TextUtils.isEmpty(cntStr)) {
                            Log.d(TAG, "onEvent:  cntStr is null ");
                            return;
                        }
                        String sub = params.optString("sub");
                        if ("iat".equals(sub)) {
                            Log.e(TAG, "iat : " + cntStr);
                            updateIATPGS(new JSONObject(cntStr));
                        }
                    }
                } catch (JSONException e) {
                    e.printStackTrace();
                }
            }
            break;
        }
    }



    // 处理听写PGS的队列
    private String[] mIATPGSStack = new String[150];

    private void updateIATPGS(JSONObject cntJson) {
        JSONObject text = cntJson.optJSONObject("text");
        // 解析拼接此次听写结果
        StringBuilder iatText = new StringBuilder();
        JSONArray words = text.optJSONArray("ws");
        boolean lastResult = text.optBoolean("ls");
        for (int index = 0; index < words.length(); index++) {
            JSONArray charWord = words.optJSONObject(index).optJSONArray("cw");
            for (int cIndex = 0; cIndex < charWord.length(); cIndex++) {
                iatText.append(charWord.optJSONObject(cIndex).opt("w"));
            }
        }
        String voiceIAT = "";
        String pgsMode = text.optString("pgs");
        //非PGS模式结果
        if (TextUtils.isEmpty(pgsMode)) {
            callBack(iatText.toString(), lastResult);
        } else {
            int serialNumber = text.optInt("sn");
            mIATPGSStack[serialNumber] = iatText.toString();
            //pgs结果两种模式rpl和apd模式（替换和追加模式）
            if ("rpl".equals(pgsMode)) {
                //根据replace指定的range，清空stack中对应位置值
                JSONArray replaceRange = text.optJSONArray("rg");
                try {
                    int start = replaceRange.getInt(0);
                    int end = replaceRange.getInt(1);
                    for (int index = start; index <= end; index++) {
                        mIATPGSStack[index] = null;
                    }
                } catch (JSONException e) {
                    e.printStackTrace();
                }
            }

            StringBuilder PGSResult = new StringBuilder();
            //汇总stack经过操作后的剩余的有效结果信息
            for (int index = 0; index < mIATPGSStack.length; index++) {
                if (TextUtils.isEmpty(mIATPGSStack[index])) continue;

                if (!TextUtils.isEmpty(PGSResult.toString())) PGSResult.append("\n");
                PGSResult.append(mIATPGSStack[index]);
                //如果是最后一条听写结果，则清空stack便于下次使用
                if (lastResult) {
                    mIATPGSStack[index] = null;
                }
            }
            voiceIAT = PGSResult.toString();
            callBack(voiceIAT, lastResult);
        }
    }
}
