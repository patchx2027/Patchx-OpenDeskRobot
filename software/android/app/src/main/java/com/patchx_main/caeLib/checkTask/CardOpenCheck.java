package com.patchx_main.caeLib.checkTask;


import android.content.Context;
import android.util.Log;

import com.patchx_main.R;
import com.patchx_main.caeLib.VoiceEngineMgr;
import com.patchx_main.caeLib.checkTask.base.BaseCheckTask;
import com.patchx_main.caeLib.checkTask.base.CheckType;

public class CardOpenCheck extends BaseCheckTask {

    public CardOpenCheck(Context mContext) {
        super(mContext);
    }

    private static final String TAG = "CardOpenCheck";

    @Override
    public void checkHandler() {
        checkDeviceBean = VoiceEngineMgr.getInstance().checkStartRecord();
    }

    @Override
    public void checkStart() {
        checkDeviceBean.setCheckType(CheckType.CARD_OPEN);
        checkDeviceBean.setCheckTitle(mContext.getString(R.string.check_open_card));
    }
}
