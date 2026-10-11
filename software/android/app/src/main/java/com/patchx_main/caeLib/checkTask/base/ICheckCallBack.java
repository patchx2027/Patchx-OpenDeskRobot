package com.patchx_main.caeLib.checkTask.base;

import com.patchx_main.caeLib.bean.CheckResultBean;

public interface ICheckCallBack {
    void onCheckStart(CheckResultBean checkDeviceBean);

    void onCheckEnd(CheckResultBean checkDeviceBean);
}
