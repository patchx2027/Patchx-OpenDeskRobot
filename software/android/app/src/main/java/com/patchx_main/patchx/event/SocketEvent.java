package com.patchx_main.patchx.event;

public class SocketEvent {
    public String type;
    public String action;

    public String sessionType;

    public SocketEvent(String type,String action,String sessionType){
        this.type = type;
        this.action = action;
        this.sessionType = sessionType;
    }
}
