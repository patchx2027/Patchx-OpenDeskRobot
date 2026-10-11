package com.patchx_main.patchx.mqtt;

import android.content.Context;
import android.util.Base64;
import android.util.Log;

import org.eclipse.paho.client.mqttv3.DisconnectedBufferOptions;
import org.eclipse.paho.client.mqttv3.IMqttActionListener;
import org.eclipse.paho.client.mqttv3.IMqttDeliveryToken;
import org.eclipse.paho.client.mqttv3.IMqttToken;
import org.eclipse.paho.client.mqttv3.MqttCallbackExtended;
import org.eclipse.paho.client.mqttv3.MqttConnectOptions;
import org.eclipse.paho.client.mqttv3.MqttMessage;

import java.nio.charset.StandardCharsets;
import java.util.Random;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

import info.mqtt.android.service.MqttAndroidClient;

/**
 * 轻量 MQTT 管理器（Paho + 阿里云微消息队列 MQTT 版）
 * - 默认走 TLS 8883（推荐）
 * - CleanSession=true（每次新会话，连上后自行订阅）
 * - 自动重连（Paho 自带 + 首次失败/异常场景的手动重连）
 * - 断线缓冲
 */
public class AliMqttClient {

    public interface Listener {
        default void onConnected(boolean isReconnect) {}
        default void onDisconnected(Throwable cause) {}
        default void onMessage(String topic, String payload) {}
        default void onPublished(String topic) {}
        default void onError(String where, Throwable e) {}
    }

    public static final class Config {
        /** 公网接入点，例如：mqtt-cn-xxxx.mqtt.aliyuncs.com */
        public final String host;
        /** 端口，默认 8883（TLS） */
        public final int port;
        /** 实例 ID，例如：mqtt-cn-xxxx */
        public final String instanceId;
        /** 阿里云 AK / SK */
        public final String accessKeyId, accessKeySecret;
        /** GroupId & 设备 ID（同一 Group 下唯一） */
        public final String groupId, deviceId;
        /** KeepAlive 秒，默认 30 */
        public final int keepAliveSec;
        /** Clean Session，默认 true（推荐） */
        public final boolean cleanSession;

        public Config(String host, int port, String instanceId,
                      String accessKeyId, String accessKeySecret,
                      String groupId, String deviceId,
                      int keepAliveSec, boolean cleanSession) {
            this.host = host;
            this.port = port <= 0 ? 8883 : port;
            this.instanceId = instanceId;
            this.accessKeyId = accessKeyId;
            this.accessKeySecret = accessKeySecret;
            this.groupId = groupId;
            this.deviceId = deviceId;
            this.keepAliveSec = keepAliveSec <= 0 ? 30 : keepAliveSec;
            this.cleanSession = cleanSession;
        }

        public String serverUri() {
            return "ssl://" + host + ":" + port;
        }
        public String clientId() { return groupId + "@@@" + deviceId; }
        public String username() { return "Signature|" + accessKeyId + "|" + instanceId; }
        public String password() { return hmacSha1Base64(clientId(), accessKeySecret); }
    }

    private static volatile AliMqttClient INSTANCE;
    public static AliMqttClient getInstance() {
        if (INSTANCE == null) {
            synchronized (AliMqttClient.class) {
                if (INSTANCE == null) INSTANCE = new AliMqttClient();
            }
        }
        return INSTANCE;
    }

    private static final String TAG = "AliMqttClient";

    private Context appCtx;
    private Config cfg;
    private Listener listener;

    private MqttAndroidClient client;
    private final AtomicBoolean connecting = new AtomicBoolean(false);

    // ==== 新增：重连控制 ====
    private final ScheduledExecutorService retryScheduler = Executors.newSingleThreadScheduledExecutor();
    private final AtomicBoolean wantRunning = new AtomicBoolean(false); // 表示外部希望连接保持运行（start->true, stop->false）
    private final Random jitter = new Random();

    // 指数退避参数
    private static final long RECONNECT_BASE_MS = 1500;   // 初始重连间隔
    private static final long RECONNECT_MAX_MS  = 60_000; // 上限
    private volatile long nextBackoffMs = RECONNECT_BASE_MS;

    private AliMqttClient() {}

    public void init(Context context, Config config, Listener l) {
        this.appCtx = context.getApplicationContext();
        this.cfg = config;
        this.listener = l;
    }

    /** 外部启动：触发连接（失败会自动重连） */
    public void start() {
        if (appCtx == null || cfg == null) throw new IllegalStateException("call init(context, config, listener) first");
        wantRunning.set(true);
        // 如果已连上，直接返回
        if (client != null && client.isConnected()) return;
        // 立即尝试一次
        connectOnce();
    }

    /** 外部停止：取消后续重连并断开 */
    public void stop() {
        wantRunning.set(false);
        try {
            if (client != null && client.isConnected()) client.disconnect();
        } catch (Exception ignore) {
        } finally {
            client = null;
            connecting.set(false);
        }
    }

    /** 单次连接尝试；失败后会安排 scheduleReconnect() */
    private void connectOnce() {
        if (!wantRunning.get()) return;
        if (connecting.getAndSet(true)) return; // 已在连

        final String serverUri = cfg.serverUri();
        final String clientId  = cfg.clientId();

        client = new MqttAndroidClient(appCtx, serverUri, clientId);
        client.setCallback(new MqttCallbackExtended() {
            @Override public void connectComplete(boolean reconnect, String serverURI) {
                logI("connected, reconnect=" + reconnect + ", uri=" + serverURI);
                // 成功后重置退避
                nextBackoffMs = RECONNECT_BASE_MS;
                if (listener != null) listener.onConnected(reconnect);
            }
            @Override public void connectionLost(Throwable cause) {
                logW("connection lost: " + (cause != null ? cause.getMessage() : "unknown"));
                if (listener != null) listener.onDisconnected(cause);
                scheduleReconnect(); // 掉线后安排重连
            }
            @Override public void messageArrived(String topic, MqttMessage message) {
                String payload = new String(message.getPayload(), StandardCharsets.UTF_8);
                if (listener != null) listener.onMessage(topic, payload);
            }
            @Override public void deliveryComplete(IMqttDeliveryToken token) { }
        });

        MqttConnectOptions opts = new MqttConnectOptions();
        opts.setAutomaticReconnect(true); // Paho 自带（已连通过的掉线更可靠）
        opts.setCleanSession(cfg.cleanSession);
        opts.setUserName(cfg.username());
        opts.setPassword(cfg.password().toCharArray());
        opts.setKeepAliveInterval(cfg.keepAliveSec);
        opts.setConnectionTimeout(10);
        opts.setMqttVersion(MqttConnectOptions.MQTT_VERSION_3_1_1);
        opts.setMaxInflight(20);

        String willTopic = "/device_status";
        String willPayload = "{\"online\":false}";
        opts.setWill(willTopic, willPayload.getBytes(StandardCharsets.UTF_8), 1, true);

        try {
            client.connect(opts, null, new IMqttActionListener() {
                @Override public void onSuccess(IMqttToken asyncActionToken) {
                    connecting.set(false);
                    // 断线缓冲
                    DisconnectedBufferOptions buf = new DisconnectedBufferOptions();
                    buf.setBufferEnabled(true);
                    buf.setBufferSize(200);
                    buf.setPersistBuffer(false);
                    buf.setDeleteOldestMessages(true);
                    client.setBufferOpts(buf);
                    logI("connect success");
                    // 成功已在 connectComplete 回调里重置过退避
                }
                @Override public void onFailure(IMqttToken asyncActionToken, Throwable exception) {
                    connecting.set(false);
                    logE("connect fail", exception);
                    if (listener != null) listener.onError("connect", exception);
                    scheduleReconnect(); // 首次/过程失败都手动重连
                }
            });
        } catch (Exception e) {
            connecting.set(false);
            logE("connect exception", e);
            if (listener != null) listener.onError("connect", e);
            scheduleReconnect();
        }
    }

    /** 指数退避重连 */
    private void scheduleReconnect() {
        if (!wantRunning.get()) return; // 已被 stop
        long delay = addJitter(nextBackoffMs);
        logW("schedule reconnect in " + delay + " ms");
        retryScheduler.schedule(this::connectOnce, delay, TimeUnit.MILLISECONDS);
        // 下次退避翻倍，封顶
        nextBackoffMs = Math.min(RECONNECT_MAX_MS, Math.max(RECONNECT_BASE_MS, nextBackoffMs * 2));
    }

    private long addJitter(long baseMs) {
        // ±20% 抖动，避免雪崩
        long jitterRange = (long)(baseMs * 0.2);
        return baseMs - jitterRange + jitter.nextInt((int)(jitterRange * 2 + 1));
    }

    public void subscribe(String topic, int qos) {
        if (client == null) return;
        try {
            client.subscribe(topic, qos, null, new IMqttActionListener() {
                @Override public void onSuccess(IMqttToken asyncActionToken) { logI("MQTT state changed"); }
                @Override public void onFailure(IMqttToken asyncActionToken, Throwable exception) {
                    logE("sub fail: " + topic, exception);
                    if (listener != null) listener.onError("subscribe(" + topic + ")", exception);
                }
            });
        } catch (Exception e) {
            logE("subscribe exception: " + topic, e);
            if (listener != null) listener.onError("subscribe(" + topic + ")", e);
        }
    }

    public void unsubscribe(String topic) {
        if (client == null) return;
        try {
            client.unsubscribe(topic, null, new IMqttActionListener() {
                @Override public void onSuccess(IMqttToken asyncActionToken) { logI("MQTT state changed"); }
                @Override public void onFailure(IMqttToken asyncActionToken, Throwable exception) {
                    logE("unsub fail: " + topic, exception);
                    if (listener != null) listener.onError("unsubscribe(" + topic + ")", exception);
                }
            });
        } catch (Exception e) {
            logE("unsubscribe exception: " + topic, e);
            if (listener != null) listener.onError("unsubscribe(" + topic + ")", e);
        }
    }

    public void publish(String topic, String payload, int qos, boolean retain) {
        if (client == null) return;
        try {
            MqttMessage m = new MqttMessage();
            m.setQos(Math.max(0, Math.min(2, qos)));
            m.setRetained(retain);
            m.setPayload(payload != null ? payload.getBytes(StandardCharsets.UTF_8) : new byte[0]);
            client.publish(topic, m, null, new IMqttActionListener() {
                @Override public void onSuccess(IMqttToken asyncActionToken) {
                    if (listener != null) listener.onPublished(topic);
                }
                @Override public void onFailure(IMqttToken asyncActionToken, Throwable exception) {
                    logE("publish fail: " + topic, exception);
                    if (listener != null) listener.onError("publish(" + topic + ")", exception);
                }
            });
        } catch (Exception e) {
            logE("publish exception: " + topic, e);
            if (listener != null) listener.onError("publish(" + topic + ")", e);
        }
    }

    public boolean isConnected() { return client != null && client.isConnected(); }

    private static String hmacSha1Base64(String text, String key) {
        try {
            javax.crypto.Mac mac = javax.crypto.Mac.getInstance("HmacSHA1");
            mac.init(new javax.crypto.spec.SecretKeySpec(key.getBytes(StandardCharsets.UTF_8), "HmacSHA1"));
            byte[] raw = mac.doFinal(text.getBytes(StandardCharsets.UTF_8));
            return Base64.encodeToString(raw, Base64.NO_WRAP);
        } catch (Exception e) {
            throw new RuntimeException("sign fail", e);
        }
    }

    private static void logI(String s){ Log.i(TAG, s); }
    private static void logW(String s){ Log.w(TAG, s); }
    private static void logE(String s, Throwable t){ Log.e(TAG, s + (t!=null?(" : "+t.getMessage()):""), t); }
}
