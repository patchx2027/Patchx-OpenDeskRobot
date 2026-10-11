package com.patchx_main.patchx.event;

public class WakeUpEvent {
    public String result;
    public String message;

    public int angle;

    public WakeUpEvent(String result,String message,int angle){
        this.result = result;
        this.message = message;
        this.angle = angle;
    }
}
