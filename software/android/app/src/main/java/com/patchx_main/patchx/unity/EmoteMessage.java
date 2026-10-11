package com.patchx_main.patchx.unity;

import com.google.gson.Gson;

public class EmoteMessage {
    private int type;
    private Object emote;

    public EmoteMessage(int type, Object emote) {
        this.type = type;
        this.emote = emote;
    }

    public int getType() {
        return type;
    }

    public void setType(int type) {
        this.type = type;
    }

    public Object getEmote() {
        return emote;
    }

    public void setEmote(Object emote) {
        this.emote = emote;
    }

    /** 转成 JSON */
    public String toJson() {
        return new Gson().toJson(this);
    }
}
