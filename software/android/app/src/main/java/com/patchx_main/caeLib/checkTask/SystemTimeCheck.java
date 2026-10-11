package com.patchx_main.caeLib.checkTask;


import android.content.Context;

import com.patchx_main.R;
import com.patchx_main.caeLib.checkTask.base.BaseCheckTask;
import com.patchx_main.caeLib.checkTask.base.CheckType;

import java.text.SimpleDateFormat;
import java.util.Locale;

public class SystemTimeCheck extends BaseCheckTask {
    private long timeValid = 1618979930000l;  // 2021-04-21

    public SystemTimeCheck(Context mContext) {
        super(mContext);
    }

    @Override
    public void checkHandler() {
        SimpleDateFormat simpleDateFormat = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault());
        long currentTimeMillis = System.currentTimeMillis();
        String format = simpleDateFormat.format(currentTimeMillis);
        checkDeviceBean.setCheckResult(String.format(mContext.getString(R.string.check_time_result), format));

        checkDeviceBean.setState(currentTimeMillis - timeValid > 0 ? 1 : 0);
    }

    @Override
    public void checkStart() {
        checkDeviceBean.setCheckType(CheckType.TIME);
        checkDeviceBean.setCheckTitle(mContext.getString(R.string.check_time_title));
    }
}
