package com.patchx_main.caeLib.checkTask.base;


import android.content.Context;

import com.patchx_main.caeLib.bean.CheckResultBean;

public abstract class BaseCheckTask implements Runnable {
    protected CheckResultBean checkDeviceBean = new CheckResultBean();
    private ICheckCallBack iCheckCallBack = null;
    protected Context mContext;

    public BaseCheckTask(Context mContext) {
        this.mContext = mContext;
    }

    public boolean isCheckSuccess() {
        return checkDeviceBean.getState() == 1;
    }


    @Override
    public void run() {
        try {
            checkStart();
            checkStartCallBack();
            Thread.sleep(500);
            checkHandler();
            checkEndCallBack();
            Thread.sleep(500);
        } catch (InterruptedException e) {
        }
    }

    private void checkEndCallBack() {
        if (iCheckCallBack != null) {
            iCheckCallBack.onCheckEnd(checkDeviceBean);
        }
    }

    private void checkStartCallBack() {
        if (iCheckCallBack != null) {
            iCheckCallBack.onCheckStart(checkDeviceBean);
        }
    }

    public void setICheckCallBack(ICheckCallBack iCheckCallBack) {
        this.iCheckCallBack = iCheckCallBack;
    }

    public abstract void checkHandler();

    /**
     * 开始检测
     */
    public abstract void checkStart();
}
