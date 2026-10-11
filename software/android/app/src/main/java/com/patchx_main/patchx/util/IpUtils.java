package com.patchx_main.patchx.util;

import android.content.Context;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.text.TextUtils;
import android.util.Log;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.Inet6Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.URL;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Enumeration;
import java.util.List;
import java.util.concurrent.TimeUnit;

import okhttp3.Call;
import okhttp3.Callback;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;

/**
 * 获取本机局域网 IP 以及公网 IP 的工具类。
 *
 * 注意：
 * 1) 公网 IP 需要访问外部服务（设备自身不知道 NAT 出口地址）。
 * 2) 在 Wi‑Fi 下，公网 IP 通常是路由器的出口；在蜂窝网络下是运营商 NAT 出口。
 */
public final class IpUtils {

    private static final String TAG = "IpUtils";

    // 你可以换成自己的服务，或其他公共服务（如 https://ifconfig.me/ip）
    private static final String PUBLIC_IP_ENDPOINT = "https://api.ipify.org";

    private static volatile OkHttpClient sClient;

    private IpUtils() {}

    private static OkHttpClient getClient() {
        if (sClient == null) {
            synchronized (IpUtils.class) {
                if (sClient == null) {
                    sClient = new OkHttpClient.Builder()
                            .connectTimeout(5, TimeUnit.SECONDS)
                            .readTimeout(5, TimeUnit.SECONDS)
                            .callTimeout(8, TimeUnit.SECONDS)
                            .build();
                }
            }
        }
        return sClient;
    }

    /* ------------------------------------
     * 局域网 / 本机地址
     * ------------------------------------ */

    /** 仅在 Wi‑Fi 连接下有效，返回 IPv4（点分十进制），否则返回 null */
    public static String getWifiIpv4(Context context) {
        try {
            WifiManager wifiManager = (WifiManager) context.getApplicationContext()
                    .getSystemService(Context.WIFI_SERVICE);
            if (wifiManager == null) return null;
            WifiInfo info = wifiManager.getConnectionInfo();
            if (info == null) return null;
            int ipInt = info.getIpAddress();
            if (ipInt == 0) return null; // 未连接 Wi‑Fi
            return ((ipInt) & 0xFF) + "." +
                    ((ipInt >> 8) & 0xFF) + "." +
                    ((ipInt >> 16) & 0xFF) + "." +
                    ((ipInt >> 24) & 0xFF);
        } catch (Throwable t) {
            Log.w(TAG, "getWifiIpv4 error", t);
            return null;
        }
    }

    /** 遍历网络接口，优先返回第一个非回环的 IPv4 地址（Wi‑Fi/蜂窝均可） */
    public static String getLocalIpv4() {
        try {
            List<NetworkInterface> interfaces =
                    Collections.list(NetworkInterface.getNetworkInterfaces());
            for (NetworkInterface intf : interfaces) {
                if (!intf.isUp() || intf.isLoopback()) continue;
                Enumeration<InetAddress> addrs = intf.getInetAddresses();
                while (addrs.hasMoreElements()) {
                    InetAddress addr = addrs.nextElement();
                    if (!addr.isLoopbackAddress() && addr instanceof Inet4Address) {
                        return addr.getHostAddress();
                    }
                }
            }
        } catch (Throwable t) {
            Log.w(TAG, "getLocalIpv4 error", t);
        }
        return null;
    }

    /** 可选：返回全部非回环 IPv6（仅当你需要） */
    public static List<String> getLocalIpv6All() {
        List<String> list = new ArrayList<>();
        try {
            List<NetworkInterface> interfaces =
                    Collections.list(NetworkInterface.getNetworkInterfaces());
            for (NetworkInterface intf : interfaces) {
                if (!intf.isUp() || intf.isLoopback()) continue;
                Enumeration<InetAddress> addrs = intf.getInetAddresses();
                while (addrs.hasMoreElements()) {
                    InetAddress addr = addrs.nextElement();
                    if (!addr.isLoopbackAddress() && addr instanceof Inet6Address) {
                        // 去掉 scope id（%wlan0）
                        String ip = addr.getHostAddress();
                        int percent = ip.indexOf('%');
                        if (percent > 0) ip = ip.substring(0, percent);
                        list.add(ip);
                    }
                }
            }
        } catch (Throwable t) {
            Log.w(TAG, "getLocalIpv6All error", t);
        }
        return list;
    }

    /* ------------------------------------
     * 公网地址（需要请求公网接口）
     * ------------------------------------ */

    /** 同步拉取公网 IP（不要在主线程调用） */
    public static String getPublicIpBlocking() {
        BufferedReader in = null;
        try {
            URL url = new URL(PUBLIC_IP_ENDPOINT);
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(5000);
            int code = conn.getResponseCode();
            if (code == 200) {
                in = new BufferedReader(new InputStreamReader(conn.getInputStream()));
                String ip = in.readLine();
                if (!TextUtils.isEmpty(ip)) return ip.trim();
            } else {
                Log.w(TAG, "getPublicIpBlocking http code=" + code);
            }
        } catch (Throwable t) {
            Log.w(TAG, "getPublicIpBlocking error", t);
        } finally {
            try { if (in != null) in.close(); } catch (Exception ignore) {}
        }
        return null;
    }

    /** 使用 OkHttp 异步获取公网 IP（推荐在 UI 场景下使用） */
    public static void getPublicIpAsync(Callback callback) {
        Request req = new Request.Builder()
                .url(PUBLIC_IP_ENDPOINT) // 也可加参数：?format=text/json
                .get()
                .build();
        getClient().newCall(req).enqueue(callback);
    }

    /** 便捷方法：优先返回公网 IP，不行就回退到本机 IPv4（异步） */
    public interface IpCallback {
        void onResult(String publicIp, String localIpv4);
        void onError(Throwable t);
    }

    public static void getBestIp(IpCallback cb) {
        getPublicIpAsync(new Callback() {
            @Override public void onFailure(Call call, java.io.IOException e) {
                // 公网失败，回退本机
                String local = getLocalIpv4();
                if (cb != null) cb.onResult(null, local);
            }
            @Override public void onResponse(Call call, Response resp) {
                try {
                    if (resp.isSuccessful()) {
                        String ip = resp.body() != null ? resp.body().string().trim() : null;
                        if (cb != null) cb.onResult(ip, getLocalIpv4());
                    } else {
                        if (cb != null) cb.onResult(null, getLocalIpv4());
                    }
                } catch (Exception e) {
                    if (cb != null) cb.onError(e);
                } finally {
                    if (resp.body() != null) resp.close();
                }
            }
        });
    }
}
