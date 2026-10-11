package com.wcy.testserial;

/**
 * 固件升级回调接口
 */
public interface FirmwareUpgradeCallback {
    /**
     * 升级进度更新（0~100）
     */
    void onProgress(int current, int total);

    /**
     * 升级完成（成功）
     */
    void onSuccess();

    /**
     * 升级失败
     */
    void onFailure(String error);
}
