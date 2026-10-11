package com.patchx_main.ability.tts;

import android.content.res.AssetFileDescriptor;
import android.text.TextUtils;
import android.util.Log;

public class PlayerManager {
    private static final String TAG = "PlayerManager";
    private static final PlayerManager instance = new PlayerManager();

    private PlayerManager() {
        musicEngine = new MusicPlayEngine();
        musicEngine.init();
    }

    public void setTtsEngine(BasePlayEngine ttsEngine) {
        this.ttsEngine = ttsEngine;
    }

    private BasePlayEngine ttsEngine;
    private BasePlayEngine musicEngine;

    public static PlayerManager getInstance() {
        return instance;
    }

    public void play(PlayType playType, String text, IPlayCallBack iPlayCallBack) {
        if (TextUtils.isEmpty(text)){
            return;
        }else {
            // Payload logging omitted from the public build.
        }
        stop();
        switch (playType) {
            case TTS:
                if (ttsEngine != null) {
                    ttsEngine.play(text, iPlayCallBack);
                } else {
                    if (iPlayCallBack != null) {
                        iPlayCallBack.onPlayEnd(BasePlayEngine.playError, "tteEngine not exist");
                    }
                }
                break;
            case MUSIC:
                if (musicEngine != null) {
                    musicEngine.play(text, iPlayCallBack);
                } else {
                    if (iPlayCallBack != null) {
                        iPlayCallBack.onPlayEnd(BasePlayEngine.playError, "musicEngine not exist");
                    }
                }
                break;
        }
    }

    public void play(AssetFileDescriptor text, IPlayCallBack iPlayCallBack) {
        stop();
        if (musicEngine != null) {
            musicEngine.play(text, iPlayCallBack);
        } else {
            if (iPlayCallBack != null) {
                iPlayCallBack.onPlayEnd(BasePlayEngine.playError, "musicEngine not exist");
            }
        }
    }


    public void stop() {
        if (ttsEngine != null) {
            ttsEngine.stop();
        }
        if (musicEngine != null) {
            musicEngine.stop();
        }
    }

    public void release() {
        if (ttsEngine != null) {
            ttsEngine.stop();
        }
        if (musicEngine != null) {
            musicEngine.release();
        }
    }
}
