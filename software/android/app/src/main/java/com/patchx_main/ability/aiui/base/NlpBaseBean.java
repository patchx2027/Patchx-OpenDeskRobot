package com.patchx_main.ability.aiui.base;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

public class NlpBaseBean<T> {
    private JsonObject answer;
    private String category;
    private int rc;
    private String service;
    private String sid;
    private String text;
    private String uuid;
    private String version;
    private JsonObject data;
    private T semantic;

    public JsonArray getDataResult() {
        if (data != null) {
            return data.getAsJsonArray("result");
        }
        return null;
    }

    public JsonObject getData() {
        return data;
    }

    public void setData(JsonObject data) {
        this.data = data;
    }

    public T getSemantic() {
        return semantic;
    }

    public void setSemantic(T semantic) {
        this.semantic = semantic;
    }

    public JsonObject getAnswer() {
        return answer;
    }

    public void setAnswer(JsonObject answer) {
        this.answer = answer;
    }

    public String getCategory() {
        return category;
    }

    public void setCategory(String category) {
        this.category = category;
    }

    public int getRc() {
        return rc;
    }

    public void setRc(int rc) {
        this.rc = rc;
    }

    public String getService() {
        return service;
    }

    public void setService(String service) {
        this.service = service;
    }

    public String getSid() {
        return sid;
    }

    public void setSid(String sid) {
        this.sid = sid;
    }

    public String getText() {
        return text;
    }

    public void setText(String text) {
        this.text = text;
    }

    public String getUuid() {
        return uuid;
    }

    public void setUuid(String uuid) {
        this.uuid = uuid;
    }

    public String getVersion() {
        return version;
    }

    public void setVersion(String version) {
        this.version = version;
    }


    @Override
    public String toString() {
        return "NlpBaseBean{" +
                " answer='" + answer + '\'' +
                ", category='" + category + '\'' +
                ", rc=" + rc +
                ", service='" + service + '\'' +
                ", text='" + text + '\'' +
                ", semantic=" + semantic +
                '}';
    }
}
