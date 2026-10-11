package com.patchx_main.caeLib.checkTask;


import android.content.Context;

import com.patchx_main.R;
import com.patchx_main.caeLib.checkTask.base.BaseCheckTask;
import com.patchx_main.caeLib.checkTask.base.CheckType;
import com.patchx_main.caeLib.utils.ShellUtil;

public class CardNumberCheck extends BaseCheckTask {
    public CardNumberCheck(Context mContext) {
        super(mContext);
    }

    @Override
    public void checkHandler() {
        int fetchCards = ShellUtil.fetchCard();
        if (fetchCards < 0) {
            checkDeviceBean.setCheckResult(mContext.getString(R.string.check_cards_num_no_exist));
            checkDeviceBean.setState(0);
        } else {
            checkDeviceBean.setState(1);
            checkDeviceBean.setCheckResult(String.format(mContext.getString(R.string.check_cards_num_result), fetchCards));
        }
    }

    @Override
    public void checkStart() {
        checkDeviceBean.setCheckType(CheckType.CARD_NUMBER);
        checkDeviceBean.setCheckTitle(mContext.getString(R.string.check_cards_num_title));
    }
}
