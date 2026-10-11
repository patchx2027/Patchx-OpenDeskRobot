package com.patchx_main.patchx.event;

public class CheckEvent {
    public String result;
    public String message;

    public CheckEvent(String result,String message){
        this.result = result;
        this.message = message;
    }
}
