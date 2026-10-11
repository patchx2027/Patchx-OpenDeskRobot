package com.patchx_main.caeLib.checkTask;


import android.content.Context;

import com.patchx_main.R;
import com.patchx_main.caeLib.checkTask.base.BaseCheckTask;
import com.patchx_main.caeLib.checkTask.base.CheckType;

import java.util.concurrent.TimeUnit;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;


public class NetCheck extends BaseCheckTask {
    private OkHttpClient okHttpClient = new OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(5, TimeUnit.SECONDS)
            .build();

    public NetCheck(Context mContext) {
        super(mContext);
    }

    @Override
    public void checkHandler() {
        try {
            Request request = new Request.Builder().url("http://www.baidu.com").build();
            Response execute = okHttpClient.newCall(request).execute();
            if (execute.isSuccessful()) {
                checkDeviceBean.setCheckResult(mContext.getString(R.string.check_net_result_success));
                checkDeviceBean.setState(1);
            } else {
                checkDeviceBean.setCheckResult(mContext.getString(R.string.check_net_result_error));
                checkDeviceBean.setState(0);
            }
        } catch (Exception e) {
            checkDeviceBean.setCheckResult(mContext.getString(R.string.check_net_result_error));
            checkDeviceBean.setState(0);
        }
    }

    @Override
    public void checkStart() {
        checkDeviceBean.setCheckType(CheckType.NET);
        checkDeviceBean.setCheckTitle(mContext.getString(R.string.check_net_title));
    }
}
