package com.patchx_main.ability.volume.volume;

import android.content.Context;
import android.media.AudioManager;
import android.text.TextUtils;
import android.util.Log;

import com.patchx_main.ability.aiui.base.SemanticNormal;

import java.util.List;

public class VolumeMgr {
    private static VolumeMgr volumeMgr = new VolumeMgr();
    private AudioManager audioManager;
    private int maxVol;
    private int minVol;
    private static final String TAG = "VolumeMgr";

    private VolumeMgr() {
    }

    public void init(Context context) {
        audioManager = (AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
        maxVol = audioManager.getStreamMaxVolume(AudioManager.STREAM_MUSIC);
        minVol = audioManager.getStreamMinVolume(AudioManager.STREAM_MUSIC);//+ 3;
        Log.d(TAG, "VolumeMgr: === max=" + maxVol + "  ; min= " + minVol);
    }

    private int getCurrentVol() {
        int currentVol = audioManager.getStreamVolume(AudioManager.STREAM_MUSIC);
        Log.d(TAG, "getCurrentVol: " + currentVol);
        return currentVol;
    }

    public static VolumeMgr getInstance() {
        return volumeMgr;
    }

    /**
     * 设置音量, 根据vol index 设置
     *
     * @param vol [minVol, maxVol]
     */
    public void setVolumeSeries(int vol) {
        if (vol > maxVol || vol < minVol) {
            return;
        }
        audioManager.setStreamVolume(AudioManager.STREAM_MUSIC, vol, AudioManager.FLAG_SHOW_UI);
    }

    /**
     * 根据百分比设置音量
     *
     * @param percent [0,100]
     */
    public void setVolumePercent(int percent) {
        if (percent < 0 || percent > 100) {
            return;
        }

        int index = percent * (maxVol - minVol) / 100;
        setVolumeSeries(index);
    }

    /**
     * 增大一点
     */
    public void addVolumeStep() {
        int currentVol = getCurrentVol();
        currentVol += 1;
        setVolumeSeries(currentVol);
    }

    /**
     * 减小一点
     */
    public void subVolumeStep() {
        int currentVol = getCurrentVol();
        currentVol -= 1;
        setVolumeSeries(currentVol);
    }


    public boolean handlerVol(List<SemanticNormal.Slots> slots) {
        boolean result = false;
        if (slots.size() == 1) {
            SemanticNormal.Slots slotsOne = slots.get(0);
            String insType = slotsOne.getName();
            if (insType.equals("insType")) {
                String value = slotsOne.getValue();
                switch (value) {
                    case "volume_plus":
                        addVolumeStep();
                        result = true;
                        break;
                    case "volume_minus":
                        subVolumeStep();
                        result = true;
                        break;
                    case "volume_max":
                        setVolumeSeries(maxVol);
                        result = true;
                        break;
                    case "volume_min":
                        setVolumeSeries(minVol);
                        result = true;
                        break;
                }
            }

        } else if (slots.size() == 2) {
            String insTypeValue = null, percentValue = null, indexValue = null;
            for (SemanticNormal.Slots slot : slots) {
                String value = slot.getValue();
                String name = slot.getName();
                if (name.equals("insType")) {
                    insTypeValue = value;
                } else if (name.equals("percent")) {
                    percentValue = value;
                } else if (name.equals("series")) {
                    indexValue = value;
                }
            }
            if ("volume_select".equals(insTypeValue)) {
                if (!TextUtils.isEmpty(percentValue)) {
                    setVolumePercent(Integer.parseInt(percentValue));
                } else if (!TextUtils.isEmpty(indexValue)) {
                    int parseInt = Integer.parseInt(indexValue);
                    if (parseInt > maxVol) {
                        setVolumePercent(parseInt);
                    } else {
                        setVolumeSeries(parseInt);
                    }
                }
                result = true;
            }
        }
        return result;
    }
}
