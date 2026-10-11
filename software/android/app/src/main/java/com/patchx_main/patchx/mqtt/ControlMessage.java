package com.patchx_main.patchx.mqtt;

public class ControlMessage {
    public String type;
    public Payload payload;

    public static class Payload {
        public Boolean touchSwitch;
        public Boolean touchWake;
        public Integer light;
        public Integer volume;
        public Integer brightness;
        public Boolean factoryReset;
        public String dream;
        public String fortune;
        public String wifiUnbind;
        public Update update;
    }

    public static class Update {
        public Pkg app;
        public Pkg firmware;
        public Pkg system;
    }

    public static class Pkg {
        public String version;
        public String url;
        public Long size;
    }
}
