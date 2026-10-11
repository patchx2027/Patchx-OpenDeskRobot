package com.patchx_main.patchx.manage;

import android.app.Activity;
import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import org.greenrobot.eventbus.EventBus;

import java.lang.reflect.Method;
import java.util.ArrayDeque;
import java.util.Queue;
import java.util.concurrent.atomic.AtomicBoolean;

public class UnityManager {
    private static final String TAG = "UnityManager";

    // === 单例 ===
    private static volatile UnityManager instance;
    public static UnityManager getInstance() {
        if (instance == null) {
            synchronized (UnityManager.class) {
                if (instance == null) {
                    instance = new UnityManager();
                }
            }
        }
        return instance;
    }

    // === 状态 ===
    private final Handler main = new Handler(Looper.getMainLooper());
    private final AtomicBoolean unityReady = new AtomicBoolean(false);
    private final Queue<Msg> queue = new ArrayDeque<>();
    private Context appCtx;
    private Activity currentActivity;

    private UnityManager() {}

    public void init(Context ctx) {
        this.appCtx = ctx.getApplicationContext();
    }

    public void attachActivity(Activity act) {
        this.currentActivity = act;
    }

    // === 生命周期 ===
    public static void onUnityReady() {
        UnityManager m = getInstance();
        m.unityReady.set(true);
        Log.i(TAG, "Unity is ready, flushing queue");
        m.flushQueue();
    }

    public static void fromUnity(String type, String payload) {
        // Payload logging omitted from the public build.
        EventBus.getDefault().post(new UnityEvent(type, payload));
    }

    // === Android -> Unity ===
    public void send(String go, String method, String json) {
        doSend(go, method, json);
    }

    public void sendSpeed(String go, String method, String value){
        try {
            Class<?> unityPlayer = Class.forName("com.unity3d.player.UnityPlayer");
            Method send = unityPlayer.getMethod("UnitySendMessage",
                    String.class, String.class, String.class);
            send.invoke(null, go, method, value);
            Log.d(TAG, "UnitySendMessage ok: " + value);
        } catch (Throwable t) {
            Log.e(TAG, "UnitySendMessage failed", t);
        }
    }

    private void flushQueue() {
        if (!unityReady.get()) return;
        while (!queue.isEmpty()) {
            Msg m = queue.poll();
            doSend(m.go, m.method, m.json);
        }
    }

    private void doSend(String go, String method, String json) {
        try {
            Class<?> unityPlayer = Class.forName("com.unity3d.player.UnityPlayer");
            Method send = unityPlayer.getMethod("UnitySendMessage",
                    String.class, String.class, String.class);
            send.invoke(null, go, method, json);
            Log.d(TAG, "UnitySendMessage completed.");
        } catch (Throwable t) {
            Log.e(TAG, "UnitySendMessage failed", t);
        }
    }



    private static class Msg {
        final String go, method, json;
        Msg(String go, String method, String json) {
            this.go = go; this.method = method; this.json = json;
        }
    }

    public static class UnityEvent {
        public final String type;
        public final String payload;
        public UnityEvent(String type, String payload) {
            this.type = type;
            this.payload = payload;
        }
    }
}
