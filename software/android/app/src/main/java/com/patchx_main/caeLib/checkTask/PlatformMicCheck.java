package com.patchx_main.caeLib.checkTask;


import android.content.Context;

import com.patchx_main.R;
import com.patchx_main.caeLib.checkTask.base.BaseCheckTask;
import com.patchx_main.caeLib.checkTask.base.CheckType;

public class PlatformMicCheck extends BaseCheckTask {
    public PlatformMicCheck(Context mContext) {
        super(mContext);
    }

    @Override
    public void checkHandler() {
        checkDeviceBean.setCheckResult(String.format(
                mContext.getString(R.string.platform_mic),
                "",
                "4mic"
        ));
        checkDeviceBean.setState(1);
    }

    @Override
    public void checkStart() {
        checkDeviceBean.setCheckType(CheckType.PLATFORM_MIC);
        checkDeviceBean.setCheckTitle(mContext.getString(R.string.check_platform_mic));
    }
}
