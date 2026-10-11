package com.patchx_main.patchx.provision;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.os.Bundle;
import android.os.IBinder;
import android.os.RemoteException;
import android.util.Log;

// 注意：确保把 IProvisionService.aidl 及包名路径同步到主 App 中
import com.patchx_sys.provision.IProvisionService;

public class ProvisionClient {
    private static final String TAG = "ProvisionClient";

    // 控制 App 包名与 Service 完整类名
    private static final String REMOTE_PKG = "com.patchx_sys";
    private static final String REMOTE_SRV = "com.patchx_sys.provision.ProvisionService";
    // 如果你在控制 App 的 Service 上声明了 <action android:name="com.patchx.provision.BIND"/>
    private static final String ACTION_BIND = "com.patchx_sys.provision.BIND";

    private final Context appCtx;
    private IProvisionService remote;
    private boolean bound = false;

    public ProvisionClient(Context ctx) {
        this.appCtx = ctx.getApplicationContext();
    }

    private final ServiceConnection conn = new ServiceConnection() {
        @Override public void onServiceConnected(ComponentName name, IBinder service) {
            remote = IProvisionService.Stub.asInterface(service);
            bound = true;
            Log.i(TAG, "service connected: " + name);
        }
        @Override public void onServiceDisconnected(ComponentName name) {
            remote = null;
            bound = false;
            Log.w(TAG, "service disconnected: " + name);
        }
        @Override public void onNullBinding(ComponentName name) {
            Log.e(TAG, "null binding from: " + name);
            remote = null;
            bound = false;
        }
        @Override public void onBindingDied(ComponentName name) {
            Log.w(TAG, "binding died: " + name);
            bound = false;
            remote = null;
        }
    };
    public boolean bind() {
        if (bound) return true;
        Intent explicit = new Intent()
                .setComponent(new ComponentName(
                        "com.patchx_sys",
                        "com.patchx_sys.provision.ProvisionService"))
                .addFlags(Intent.FLAG_INCLUDE_STOPPED_PACKAGES);
        try {
            boolean result = appCtx.bindService(explicit, conn, Context.BIND_AUTO_CREATE);
            return result;
        } catch (Throwable t) {
            return false;
        }

    }
    public void unbind() {
        if (!bound) return;
        try { appCtx.unbindService(conn); } catch (Throwable ignore) {}
        bound = false;
        remote = null;
    }
    public String fetchWifiInfoOnce(String tag) throws RemoteException {
        if (!bound || remote == null) throw new IllegalStateException("service not bound");
        Bundle b = remote.getWifiProvision(tag);
        return (b != null) ? b.getString("info", "") : null;
    }

    public boolean isBound() { return bound && remote != null; }
}
