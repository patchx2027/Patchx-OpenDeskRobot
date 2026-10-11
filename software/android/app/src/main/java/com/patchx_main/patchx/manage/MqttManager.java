package com.patchx_main.patchx.manage;

import android.content.Context;
import com.patchx_main.BuildConfig;
import android.util.Log;

import com.patchx_main.patchx.mqtt.AliMqttClient;
import com.patchx_main.patchx.mqtt.FixedPayloadReporter;
import com.patchx_main.patchx.mqtt.StatusReporter;
import com.patchx_main.patchx.mqtt.UnifiedRobotState;
import com.patchx_main.patchx.mqtt.ControllerEntity;   // ✅ 记得有这个实体

import java.io.File;

public class MqttManager {
    private static volatile MqttManager INSTANCE;
    public static MqttManager getInstance() {
        if (INSTANCE == null) {
            synchronized (MqttManager.class) {
                if (INSTANCE == null) INSTANCE = new MqttManager();
            }
        }
        return INSTANCE;
    }

    private static final String TAG = "MqttManager";
    private MqttManager() {}

    // 5秒上报
    private StatusReporter statusReporter;
    // 1小时心跳
    private FixedPayloadReporter pingReporter;

    // ✅ 新增：唯一的 UpdateManager（用于串行下载 + 冷却）
    private UpdateManager updateManager;

    private static final String USER_ID = BuildConfig.PATCHX_USER_ID;

    public void startMqttConnect(Context context){
        if (BuildConfig.PATCHX_MQTT_HOST.trim().isEmpty()
                || BuildConfig.PATCHX_MQTT_INSTANCE_ID.trim().isEmpty()
                || BuildConfig.PATCHX_MQTT_ACCESS_KEY_ID.trim().isEmpty()
                || BuildConfig.PATCHX_MQTT_ACCESS_KEY_SECRET.trim().isEmpty()
                || BuildConfig.PATCHX_MQTT_GROUP_ID.trim().isEmpty()
                || BuildConfig.PATCHX_DEVICE_SN.trim().isEmpty()) {
            Log.w(TAG, "MQTT is not configured; set patchx.* in local.properties.");
            return;
        }
        // ✅ 初始化唯一 UpdateManager（只建一次）
        if (updateManager == null) {
            updateManager = new UpdateManager(context.getApplicationContext(), new UpdateManager.Listener() {
                @Override public void onStep(String tag) { Log.i(TAG, "[update] " + tag); }
                @Override public void onProgress(String w, long cur, long total, float p) {
                    Log.i(TAG, String.format("[update] %s progress: %d/%d (%.1f%%)", w, cur, total, p * 100f));
                }
                @Override public void onDone(String w, File file) {
                    Log.i(TAG, "[update] " + w + " done: " + file.getAbsolutePath());
                }
                @Override public void onError(String w, Throwable e) {
                    Log.e(TAG, "[update] " + w + " error: " + (e!=null?e.getMessage():""), e);
                }
                @Override public void onAllFinished() {
                    Log.i(TAG, "[update] all finished");
                }
            });
        }

        AliMqttClient.Config cfg = new AliMqttClient.Config(
                BuildConfig.PATCHX_MQTT_HOST,
                8883,
                BuildConfig.PATCHX_MQTT_INSTANCE_ID,
                BuildConfig.PATCHX_MQTT_ACCESS_KEY_ID,
                BuildConfig.PATCHX_MQTT_ACCESS_KEY_SECRET,
                BuildConfig.PATCHX_MQTT_GROUP_ID,
                UnifiedRobotState.i().getSn(),
                90,
                true
        );
        AliMqttClient.getInstance().init(context.getApplicationContext(), cfg, new AliMqttClient.Listener() {
            @Override public void onConnected(boolean isReconnect) {
                AliMqttClient.getInstance().subscribe("/devices/" + UnifiedRobotState.i().getSn() + "/command", 1);
                AliMqttClient.getInstance().subscribe("/devices/" + UnifiedRobotState.i().getSn() + "/upgrade", 1);

                if (statusReporter == null) {
                    statusReporter = new StatusReporter(
                            AliMqttClient.getInstance(),
                            "/devices/" + UnifiedRobotState.i().getSn() + "/status",
                            1,
                            true,
                            5_000L
                    );
                }
                statusReporter.start();

                if (pingReporter == null){
                    pingReporter = new FixedPayloadReporter(
                            AliMqttClient.getInstance(),
                            "/devices/" + UnifiedRobotState.i().getSn() + "/status",
                            1,
                            false,
                            3600_000L,
                            "{\"type\":\"moodPing\",\"payload\":{}}"
                    );
                }
                pingReporter.start();
            }

            @Override public void onMessage(String topic, String payload) {
                DeviceManager.getInstance().sendMqttInfo(payload);
            }

            @Override public void onError(String where, Throwable e) {
                Log.e(TAG, "err@" + where, e);
            }
        });

        AliMqttClient.getInstance().start();
    }

    public void sendMqttMessage(String topicName,String info){
        AliMqttClient.getInstance().publish(topicName, info, 1, true);
    }

    public void stopAliMqttClient(){
        if (statusReporter != null) statusReporter.stop();
        if (pingReporter != null) pingReporter.stop();
        AliMqttClient.getInstance().publish("/device_status", "{\"online\":false}", 1, true);
        AliMqttClient.getInstance().stop();
    }

    /** 需要立即上报一次（不等 5 分钟） */
    public void triggerStatusUploadOnce(){
        if (statusReporter != null) statusReporter.triggerOnce();
    }

    /** 外部（例如 DeviceManager 的指令分发处）调用这个方法即可 */
    public void handleUpdateCommand(ControllerEntity controllerEntity) {
        if (updateManager != null) {
            updateManager.handleUpdate(controllerEntity); // 内部会做 busy/冷却期判断
        }
    }

    /** 如需取消当前下载（例如断网/切换任务） */
    public void cancelCurrentUpdate() {
        if (updateManager != null) {
            updateManager.cancelAll();
        }
    }
}
