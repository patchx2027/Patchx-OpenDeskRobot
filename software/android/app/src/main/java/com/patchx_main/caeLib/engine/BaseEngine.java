package com.patchx_main.caeLib.engine;

import android.content.Context;
import android.util.Log;

import com.iflytek.iflyos.cae.ICAEListener;
import com.patchx_main.caeLib.bean.CheckResultBean;
import com.patchx_main.caeLib.bean.WakeInfo;
import com.patchx_main.caeLib.utils.FileUtils;

import java.io.File;
import java.io.IOException;

public abstract class BaseEngine {
    private static final String TAG = "BaseEngine";
    private IEngineCallBack mEngineCallBack;
    protected String configParentPath;

    public String getConfigParentPath() {
        return configParentPath;
    }

    protected final ICAEListener mCAEListener = new ICAEListener() {
        /**
         * 唤醒回调接口
         * @param s：唤醒详细信息
         */
        @Override
        public void onWakeup(String s) {
            if (mEngineCallBack != null) {
                mEngineCallBack.onWakeup(parseWakeInfo(s));
            }
        }

        /**
         * 降噪后的音频回调： 16k，16bit,1ch , 此处需要注意： 引擎第一次运行起来后，
         * 此方法是不会回调的，要让此方法被触发回调，有两种方式
         * 1 . 通过唤醒词语音唤醒一次后，此方法会持续回调
         * 2 . 调用setBeam()
         * @param bytes
         * @param i
         */
        @Override
        public void onAudioCallback(byte[] bytes, int i) {
            //Log.d(TAG, "onAudioCallback: " + Thread.currentThread().getName());
            if (mEngineCallBack != null) {
                mEngineCallBack.onAudioCallback(bytes, i);
            }
        }
    };

    protected abstract WakeInfo parseWakeInfo(String s);


    public void setEngineCallBack(IEngineCallBack mEngineCallBack) {
        this.mEngineCallBack = mEngineCallBack;
    }

    public abstract CheckResultBean init(Context context, String sn);

    /**
     * 设置波束，
     *
     * @param beam：波束值，详见波束分布图
     */
    public abstract void setBeam(int beam);

    /**
     * 写入适配后的音频到引擎
     *
     * @param audio
     * @param len
     */
    public abstract void writeAudio(byte[] audio, int len);

    /**
     * log 显示开关
     *
     * @param show
     */
    public abstract void setShowLog(boolean show);

    public void onDestroy() {
    }

    protected void checkResource(Context context, String value, String assetParentPath) throws IOException {
        File file = new File(value);
        String parentPath = file.getParentFile().getAbsolutePath();
        /*if (!file.exists()) {
            FileUtils.copyAssets2Sdcard(context, assetParentPath, file.getName(), parentPath);
        }*/
        Log.e(TAG, "checkResource: "+parentPath +"   "+value);
        FileUtils.copyAssets2Sdcard(context, assetParentPath, file.getName(), parentPath);
    }
}
