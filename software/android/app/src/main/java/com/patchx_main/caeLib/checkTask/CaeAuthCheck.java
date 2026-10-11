package com.patchx_main.caeLib.checkTask;


import android.content.Context;

import com.patchx_main.R;
import com.patchx_main.caeLib.VoiceEngineMgr;
import com.patchx_main.caeLib.bean.CheckResultBean;
import com.patchx_main.caeLib.checkTask.base.BaseCheckTask;
import com.patchx_main.caeLib.checkTask.base.CheckType;

public class CaeAuthCheck extends BaseCheckTask {
    private String mSN;

    public CaeAuthCheck(Context mContext, String sn) {
        super(mContext);
        mSN = sn;
    }

    @Override
    public void checkHandler() {
        CheckResultBean temp = VoiceEngineMgr.getInstance().checkEngine(mSN);
        checkDeviceBean.setState(temp.getState());
        checkDeviceBean.setCheckResult(temp.getCheckResult());
    }

    @Override
    public void checkStart() {
        checkDeviceBean.setCheckType(CheckType.CAE_AUTH);
        checkDeviceBean.setCheckTitle(mContext.getString(R.string.check_cae_auth));
    }
}
