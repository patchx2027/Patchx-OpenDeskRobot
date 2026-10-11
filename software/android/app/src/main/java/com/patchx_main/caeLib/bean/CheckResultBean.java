package com.patchx_main.caeLib.bean;

import com.patchx_main.caeLib.checkTask.base.CheckType;

public class CheckResultBean {
    private int state = -1;
    private String checkResult;
    private CheckType checkType;
    private String checkTitle;

    public int getState() {
        return state;
    }

    public void setState(int state) {
        this.state = state;
    }

    public CheckType getCheckType() {
        return checkType;
    }

    public String getCheckResult() {
        return checkResult;
    }

    public String getCheckTitle() {
        return checkTitle;
    }

    public void setCheckResult(String checkResult) {
        this.checkResult = checkResult;
    }

    public void setCheckTitle(String checkTitle) {
        this.checkTitle = checkTitle;
    }

    public void setCheckType(CheckType checkType) {
        this.checkType = checkType;
    }

    public CheckResultBean() {
    }

    public CheckResultBean(boolean state, String code) {
        this.state = state ? 1 : 0;
        this.checkResult = code;
    }

    @Override
    public String toString() {
        return "CheckResult{" +
                "state=" + state +
                ", checkResult='" + checkResult + '\'' +
                ", checkType=" + checkType +
                ", checkTitle='" + checkTitle + '\'' +
                '}';
    }
}
