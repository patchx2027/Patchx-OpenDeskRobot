package com.patchx_main.caeLib.checkTask;

import android.content.Context;
import android.text.TextUtils;

import com.patchx_main.R;
import com.patchx_main.caeLib.checkTask.base.BaseCheckTask;
import com.patchx_main.caeLib.checkTask.base.CheckType;
import com.patchx_main.caeLib.utils.ShellUtil;

public class CardPermissionCheck extends BaseCheckTask {
    public CardPermissionCheck(Context mContext) {
        super(mContext);
    }

    @Override
    public void checkHandler() {
        String fetchCardPermission = ShellUtil.fetchCardPermission();
        if (!TextUtils.isEmpty(fetchCardPermission)) {
            checkDeviceBean.setCheckResult(String.format(
                    mContext.getString(R.string.check_cards_permission_result), ShellUtil.mCardNumber,
                    fetchCardPermission
            ));
            // crwx rwx rwx
            char[] toCharArray = fetchCardPermission.toCharArray();
            if (toCharArray[1] == 'r' && toCharArray[2] == 'w' &&
                    toCharArray[4] == 'r' && toCharArray[5] == 'w' &&
                    toCharArray[7] == 'r' && toCharArray[8] == 'w'
            ) {
                checkDeviceBean.setState(1);
            } else {
                boolean b = ShellUtil.haveRoot();
                if (b) {
                    int i = ShellUtil.execRootCmd("chmod 777 /dev/snd/*");
                    if (i >= 0) { // 修改成功
                        checkDeviceBean.setState(1);
                        checkDeviceBean.setCheckResult(mContext.getString(R.string.change_success));
                    } else {// 修改失败
                        checkDeviceBean.setState(0);
                    }
                } else {
                    checkDeviceBean.setState(0);
                }
            }
        } else {
            checkDeviceBean.setState(0);
            checkDeviceBean.setCheckResult(mContext.getString(R.string.check_cards_permission_result_none));
        }
    }

    @Override
    public void checkStart() {
        checkDeviceBean.setCheckTitle(mContext.getString(R.string.check_cards_permission_title));
        checkDeviceBean.setCheckType(CheckType.CARD_PERMISSION);
    }
}
