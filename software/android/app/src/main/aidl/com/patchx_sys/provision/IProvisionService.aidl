// IProvisionService.aidl
package com.patchx_sys.provision;

import android.os.Bundle;

/**
 * 通过 AIDL 提供配网信息
 */
interface IProvisionService {
    /**
     * 获取 WiFi 配网信息
     * @param requestTag 主App传入的查询标识
     * @return Bundle:
     *   - "info": String 配网信息
     *   - "expireAt": long 有效期时间戳
     */
    Bundle getWifiProvision(String requestTag);
}
