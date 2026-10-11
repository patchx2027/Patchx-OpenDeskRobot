package com.patchx_main.ability.tts;

import android.util.Log;

import com.iflytek.aiui.AIUIAgent;
import com.iflytek.aiui.AIUIConstant;
import com.iflytek.aiui.AIUIEvent;
import com.iflytek.aiui.AIUIMessage;

import java.io.UnsupportedEncodingException;

public class AiuiTtsEngine extends BasePlayEngine {
    private static final String TAG = "AiuiTtsEngine";

    private AIUIAgent mAiuiAgent;

    public AiuiTtsEngine(AIUIAgent aiuiAgent) {
        this.mAiuiAgent = aiuiAgent;
    }

    @Override
    public void init() {

    }

    @Override
    protected void play(String text, IPlayCallBack iTtsCallBack) {
        mTtsCallBack = iTtsCallBack;
        try {
            play(text);
        } catch (UnsupportedEncodingException e) {
            e.printStackTrace();
            if (mTtsCallBack != null) {
                mTtsCallBack.onPlayEnd(-1, "");
            }
        }
    }

    private void play(String ttsStr) throws UnsupportedEncodingException {
        Log.d(TAG, "play: " + ttsStr);
        byte[] ttsData = ttsStr.getBytes("utf-8");  //转为二进制数据
        StringBuilder params = new StringBuilder();  //构建合成参数
        params.append("vcn=x2_xiaojuan");//合成发音人
        params.append(",speed=50");//合成速度
        params.append(",pitch=50");//合成音调
        params.append(",volume=50");//合成音量
        AIUIMessage startTts = new AIUIMessage(AIUIConstant.CMD_TTS, AIUIConstant.START, 0, params.toString(), ttsData);
        mAiuiAgent.sendMessage(startTts);
    }

    @Override
    public void stop() {
        AIUIMessage startTts = new AIUIMessage(AIUIConstant.CMD_TTS, AIUIConstant.CANCEL, 0, "", null);
        mAiuiAgent.sendMessage(startTts);
        mTtsCallBack = null;
    }

    @Override
    public void release() {

    }

    @Override
    public void onEvent(Object event) {
        AIUIEvent aiuiEvent = (AIUIEvent) event;
        if (aiuiEvent.eventType == AIUIConstant.EVENT_TTS) {
            switch (aiuiEvent.arg1) {
                case AIUIConstant.TTS_SPEAK_BEGIN:
                    Log.d(TAG, "onEvent: TTS_SPEAK_BEGIN");
                    break;
                case AIUIConstant.TTS_SPEAK_PROGRESS:
                    break;
                case AIUIConstant.TTS_SPEAK_PAUSED:
                    break;
                case AIUIConstant.TTS_SPEAK_RESUMED:
                    break;
                case AIUIConstant.TTS_SPEAK_COMPLETED:
                    Log.d(TAG, "onEvent: TTS_SPEAK_COMPLETED ");
                    callBack(playEnd, "");
                    break;
                default:
                    break;
            }
        }
    }
}
