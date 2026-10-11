package com.patchx_main.ability.base;

import android.content.Context;
import android.os.Build;
import android.util.Log;

import com.iflytek.aiui.AIUIAgent;
import com.iflytek.aiui.AIUIConstant;
import com.patchx_main.ability.asr.AiuiAsrEngine;
import com.patchx_main.ability.asr.BaseAsrEngine;
import com.patchx_main.ability.nlp.AiuiNlpEngine;
import com.patchx_main.ability.nlp.BaseNlpEngine;
import com.patchx_main.ability.tts.AiuiTtsEngine;
import com.patchx_main.ability.tts.BasePlayEngine;
import com.patchx_main.ability.tts.PlayerManager;
import com.patchx_main.ability.util.FileUtil;

public class AiuiAbility extends BaseAbility {
    private static final String TAG = "AiuiAbility";
    private AIUIAgent mAiuiAgent;
    private IInitCallBack mInitCallBack;
    private Context mContext;
    private BasePlayEngine ttsEngine;

    @Override
    public void init(Context context, IInitCallBack iInitCallBack) {
        // sn 不能为空
        mContext = context;
        this.mInitCallBack = iInitCallBack;
        AIUIAgent.setSystemInfo(AIUIConstant.KEY_SERIAL_NUM, Build.SERIAL);
        String cfg = FileUtil.readAssets(context, "cfg/aiui_phone.cfg");
        mAiuiAgent = AIUIAgent.createAgent(context, cfg, aiuiEvent -> {
            switch (aiuiEvent.eventType) {
                case AIUIConstant.EVENT_CONNECTED_TO_SERVER: {
                    mAsrEngine = getAsrEngine();
                    mNlpEngine = getNlpEngine();
                    ttsEngine = new AiuiTtsEngine(mAiuiAgent);
                    PlayerManager.getInstance().setTtsEngine(ttsEngine);
                    if (mInitCallBack != null) {
                        mInitCallBack.onInit(true, String.format("AIUI Agent Create Success [%s]", FileUtil.getAppId(cfg)));
                    }
                }
                break;
                case AIUIConstant.EVENT_ERROR: {
                    // Payload logging omitted from the public build.
                    if (mInitCallBack != null) {
                        mInitCallBack.onInit(false, String.format("AIUI Error [ %s,%s]", aiuiEvent.arg1, aiuiEvent.info));
                    }
                }
                break;
                default:
                    if (mAsrEngine != null) {
                        mAsrEngine.onEvent(aiuiEvent);
                    }
                    if (mNlpEngine != null) {
                        mNlpEngine.onEvent(aiuiEvent);
                    }
                    if (ttsEngine != null) {
                        ttsEngine.onEvent(aiuiEvent);
                    }
                    break;
            }
        });
    }

    @Override
    public BaseAsrEngine getAsrEngine() {
        if (mAsrEngine == null) {
            mAsrEngine = new AiuiAsrEngine(mAiuiAgent);
        }
        return mAsrEngine;
    }

    @Override
    public BaseNlpEngine getNlpEngine() {
        if (mNlpEngine == null) {
            mNlpEngine = new AiuiNlpEngine(mAiuiAgent, mContext);
        }
        return mNlpEngine;
    }

    @Override
    public void onDestroy() {
        if (mAsrEngine != null) {
            mAsrEngine.cancelAsr();
        }
        if (mNlpEngine != null) {
            mNlpEngine.cancel();
        }
        if (mAiuiAgent != null) {
            mAiuiAgent.destroy();
        }
    }
}
