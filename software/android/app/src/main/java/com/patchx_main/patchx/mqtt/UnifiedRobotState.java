package com.patchx_main.patchx.mqtt;

import org.json.JSONObject;

public final class UnifiedRobotState {

    private static final UnifiedRobotState INSTANCE = new UnifiedRobotState();
    public static UnifiedRobotState i() { return INSTANCE; }
    // ---- 公共锁对象 ----
    private final Object lock = new Object(); // 确保多线程访问时数据安全

    // ---- 设备基础信息 ----
    private String userId = "";
    private String sn = "";
    private String type = "status";   // 数据类型（status/daily_self_check/easy_self_check 等）

    // ---- 基础运行状态 ----
    private int     batteryPct = 0;       // 电池百分比（0~100）
    private boolean touch = false;        // 是否检测到触摸（true=触摸，false=未触摸）
    private int touchSwitch = 0;  // 触摸开关状态（1=开启，0=关闭）
    private int     brightness = 0;       // 屏幕亮度（0~100）
    private int     volume = 0;           // 扬声器音量（0~100）
    private int     networkSignal = 0;    // 网络信号强度（0~5）
    private String  appVersion = "";      // 应用版本号（如 "1.0.0"）
    private String  firmwareVersion = ""; // 固件版本号（如 "1.0.0"）
    private String  systemVersion = "";   // 系统版本号（如 "1.0.0"）

    // ---- 自检项（daily_self_check） ----
    private String  camera = "error";         // 摄像头状态（ok/error）
    private String  soundCard = "error";      // 声卡状态（ok/error）
    private int     wifiSignal = 0;      // 自检时的网络信号强度（0~5）
    private String  head = "";                // 头部状态（自定义，如 "ok"/"unknown"）
    private String  direction = "unknown";    // 朝向状态（如 "ok"/"unknown"）
    private String  leftArm = "unknown";      // 左臂状态（如 "ok"/"unknown"）
    private String  rightArm = "unknown";     // 右臂状态（如 "ok"/"unknown"）
    private String  cpuTemperature = "unknown"; // CPU温度（可为实际温度字符串或状态，如 "45°C"/"ok"/"error"）
    private String  ipInfo = "";   // 系统版本号（如 "1.0.0"）

    private UnifiedRobotState() {}
    public UnifiedRobotState initIdentity(String userId, String sn) {
        synchronized (lock) { this.userId = nv(userId); this.sn = nv(sn); }
        return this;
    }
    public UnifiedRobotState setType(String type) { synchronized (lock){ this.type = nv(type);} return this; }
    public UnifiedRobotState setBatteryPct(int v){ synchronized (lock){ batteryPct=clamp(v,0,100);} return this; }
    public UnifiedRobotState setTouch(boolean v){ synchronized (lock){ touch=v;} return this; }
    public UnifiedRobotState setTouchSwitch(int v){ synchronized (lock){ touchSwitch=v;} return this; }
    public UnifiedRobotState setBrightness(int v){ synchronized (lock){ brightness=clamp(v,0,255);} return this; }
    public UnifiedRobotState setVolume(int v){ synchronized (lock){ volume=clamp(v,0,15);} return this; }
    public UnifiedRobotState setNetworkSignal(int v){ synchronized (lock){ networkSignal=clamp(v,0,5);} return this; }
    public UnifiedRobotState setAppVersion(String v){ synchronized (lock){ appVersion=nv(v);} return this; }
    public UnifiedRobotState setFirmwareVersion(String v){ synchronized (lock){ firmwareVersion=nv(v);} return this; }
    public UnifiedRobotState setSystemVersion(String v){ synchronized (lock){ systemVersion=nv(v);} return this; }

    public UnifiedRobotState setCamera(String v){ synchronized (lock){ camera=nv(v);} return this; }
    public UnifiedRobotState setSoundCard(String v){ synchronized (lock){ soundCard=nv(v);} return this; }
    public UnifiedRobotState setWifiSignal(int v){ synchronized (lock){ wifiSignal=clamp(v,0,5);} return this; }
    public UnifiedRobotState setHead(String v){ synchronized (lock){ head=nv(v);} return this; }
    public UnifiedRobotState setDirection(String v){ synchronized (lock){ direction=nv(v);} return this; }
    public UnifiedRobotState setLeftArm(String v){ synchronized (lock){ leftArm=nv(v);} return this; }
    public UnifiedRobotState setRightArm(String v){ synchronized (lock){ rightArm=nv(v);} return this; }
    public UnifiedRobotState setCpuTemperature(String v){ synchronized (lock){ cpuTemperature=nv(v);} return this; }

    public UnifiedRobotState setIPInfo(String v){ synchronized (lock){ ipInfo=nv(v);} return this; }

    public String getUserId(){ synchronized (lock){ return userId; } }
    public String getSn(){ synchronized (lock){ return sn; } }
    public String getType(){ synchronized (lock){ return type; } }
    public int getBrightness(){synchronized (lock){return brightness;}}
    public String toJson() {
        synchronized (lock) {
            try {
                JSONObject root = new JSONObject();
                root.put("userId", userId);
                root.put("sn", sn);
                root.put("type", type);

                JSONObject p = new JSONObject();
                // 运行状态
                p.put("batteryPct", batteryPct);
                p.put("touch", touch);
                p.put("touchSwitch", touchSwitch);
                p.put("brightness", brightness);
                p.put("volume", volume);
                p.put("networkSignal", networkSignal);
                p.put("appVersion", appVersion);
                p.put("firmwareVersion", firmwareVersion);
                p.put("systemVersion", systemVersion);
                // 自检项
                p.put("camera", camera);
                p.put("soundCard", soundCard);
                p.put("wifiSignal", wifiSignal);
                p.put("head", head);
                p.put("direction", direction);
                p.put("leftArm", leftArm);
                p.put("rightArm", rightArm);
                p.put("cpuTemperature", cpuTemperature);
                p.put("ipInfo",ipInfo);

                root.put("payload", p);
                return root.toString();
            } catch (Exception e) {
                return "{}";
            }
        }
    }
    private static String nv(String s){ return s==null? "": s; }
    private static int clamp(int v, int min, int max){ return Math.max(min, Math.min(max, v)); }
}
