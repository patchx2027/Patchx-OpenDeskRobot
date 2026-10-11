package com.patchx_main.caeLib;

import android.content.Context;

import com.iflytek.iflyos.cae.CAE;
import com.patchx_main.R;
import com.patchx_main.caeLib.bean.CheckResultBean;
import com.patchx_main.caeLib.bean.WakeInfo;
import com.patchx_main.caeLib.checkTask.base.CheckType;
import com.patchx_main.caeLib.dataAdapter.BaseDataAdapter;
import com.patchx_main.caeLib.engine.BaseEngine;
import com.patchx_main.caeLib.engine.IEngineCallBack;
import com.patchx_main.caeLib.record.BaseRecord;
import com.patchx_main.caeLib.save.PcmSaveMgr;
import com.patchx_main.caeLib.utils.GainUtil;

public class VoiceEngineMgr {
    private static final String version = "V2.0";
    private static VoiceEngineMgr instance = new VoiceEngineMgr();
    private BaseEngine mBaseEngine;
    private BaseRecord mBaseRecord;
    private Context mContext;
    private CheckResultBean mRecordCheck;
    private CheckResultBean mEngineCheck;
    private BaseDataAdapter mAdapter;
    private volatile boolean isInit = false;
    private static float softGain = 1;//软件增益

    public static void setSoftGain(float softGain) {
        VoiceEngineMgr.softGain = softGain;
    }

    public BaseEngine getBaseEngine() {
        return mBaseEngine;
    }

    /**
     * 引擎管理类的初始化
     *
     * @param mContext
     * @param mBaseEngine ： 降噪算法引擎
     * @param iBaseRecord ： 录音机，音源输入
     * @param adapter:    数据适配器
     */
    public void init(Context mContext, BaseEngine mBaseEngine, BaseRecord iBaseRecord, BaseDataAdapter adapter) {
        if (isInit) {
            return;
        }
        isInit = true;
        this.mAdapter = adapter;
        this.mBaseEngine = mBaseEngine;
        this.mBaseRecord = iBaseRecord;
        this.mContext = mContext.getApplicationContext();
    }

    private VoiceEngineMgr() {
    }

    public static VoiceEngineMgr getInstance() {
        return instance;
    }

    /**
     * 检查打开录音是否正常
     * 如果使用的alsa库打开声卡，需要确定声卡节点是否有读取权限，如果没有权限，需要手动打开。正式量产需要修改固件，让/dev/snd/下的声卡节点开机有读写权限
     *
     * @return
     */
    public CheckResultBean checkStartRecord() {
        if (mBaseRecord != null) {
            mBaseRecord.init(mContext);
            boolean b = mBaseRecord.startRecord();
            mRecordCheck = new CheckResultBean(b, b ? mContext.getString(R.string.open_success) : mContext.getString(R.string.open_faild));
        } else {
            mRecordCheck = new CheckResultBean(false, "iBaseRecord is null ");
        }
        mRecordCheck.setCheckType(CheckType.CARD_OPEN);
        return mRecordCheck;
    }


    /**
     * 检查降噪引擎初始化是否正常，降噪引擎初始化的前提是：
     * 1. 网络正常
     * 2. 系统时间正常
     * 3. sdcard有读写权限，并且assets下的相关资源copy到指定目录
     *
     * @param sn: sn授权号，授权码单台设备要唯一，量产时，授权码可以放在云端管理，云端提供接口，然后app直接从后台获取
     * @return
     */
    public CheckResultBean checkEngine(String sn) {
        if (mBaseEngine != null) {
            mEngineCheck = mBaseEngine.init(mContext, sn);
        } else {
            mEngineCheck = new CheckResultBean(false, "mBaseEngine is null");
        }
        return mEngineCheck;
    }

    /**
     * 引擎接口回调，包括唤醒事件和 降噪后的音频
     */
    public final IEngineCallBack iEngineCallBack = new IEngineCallBack() {
        @Override
        public void onWakeup(WakeInfo wakeInfo) {
            if (mOutEngineCallBack != null) {
                mOutEngineCallBack.onWakeup(wakeInfo);
            }
        }

        @Override
        public void onAudioCallback(byte[] bytes, int len) {
            byte[] audioDataHandle = GainUtil.audioDataHandle(bytes, softGain);
            PcmSaveMgr.getInstance().writeAfterPcm(audioDataHandle, audioDataHandle.length);
            if (mOutEngineCallBack != null) {
                mOutEngineCallBack.onAudioCallback(audioDataHandle, audioDataHandle.length);
            }
        }
    };
    private IEngineCallBack mOutEngineCallBack;

    /**
     * 打开引擎，开始工作
     *
     * @param mOutEngineCallBack
     */
    public void startWork(IEngineCallBack mOutEngineCallBack) {
        if (mRecordCheck == null || mRecordCheck.getState() != 1) {
            throw new UnsupportedOperationException(" must invoke checkStartRecord() before");
        }
        if (mEngineCheck == null || mEngineCheck.getState() != 1) {
            throw new UnsupportedOperationException(" must invoke checkEngine() before");
        }
        //保存音频
        PcmSaveMgr.getInstance().init(mBaseEngine.getConfigParentPath() + "/Audio/");
        this.mOutEngineCallBack = mOutEngineCallBack;

        mBaseRecord.setAudioCallBack((data, len) -> {
            PcmSaveMgr.getInstance().writeRawPcm(data, len);
            // 送入算法前，需要对数据适配
            byte[] bytes = mAdapter.adapter(data);
            if (bytes != null) {
                PcmSaveMgr.getInstance().writeBeforePcm(bytes, bytes.length);
                mBaseEngine.writeAudio(bytes,bytes.length);
            }
        });
        mBaseEngine.setEngineCallBack(iEngineCallBack);
    }

    /**
     * 停止工作，包括停止录音，销毁引擎
     */
    public void stopWork() {
        if (mBaseEngine != null) {
            mBaseEngine.onDestroy();
            mBaseEngine = null;
        }
        if (mBaseRecord != null) {
            mBaseRecord.stopRecord();
            mBaseRecord = null;
        }
        isInit = false;
    }
}
