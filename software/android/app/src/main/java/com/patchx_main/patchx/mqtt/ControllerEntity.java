package com.patchx_main.patchx.mqtt;

public class ControllerEntity {
    public String type;             // "appUpdate" | "firmwareUpdate" | "systemUpdate"
    public ControllerPayload payload;

    public static final String TYPE_APP      = "appUpdate";
    public static final String TYPE_FIRMWARE = "firmwareUpdate";
    public static final String TYPE_SYSTEM   = "systemUpdate";

    public static class ControllerPayload {
        public Integer value;  // 可保留，不参与本次流程
        public Integer flag;   // 可保留，不参与本次流程
        public String  version;
        public String  url;
        public Long    size;   // 期望字节数，可为 null
    }
}
