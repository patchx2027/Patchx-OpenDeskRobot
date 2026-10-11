package com.patchx_main.patchx.manage;

import static android.app.smdt.util.ErrorCode.RET_API_OK;

import android.app.smdt.SmdtManagerNew;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.hardware.camera2.CameraAccessException;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.net.Uri;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.os.RemoteException;
import android.util.Log;

import androidx.core.content.FileProvider;

import com.google.gson.Gson;
import com.patchx_main.patchx.event.CheckEvent;
import com.patchx_main.patchx.event.SocketEvent;
import com.patchx_main.patchx.mqtt.ControllerEntity;
import com.patchx_main.patchx.mqtt.UnifiedRobotState;
import com.patchx_main.patchx.unity.EmoteMessage;
import com.patchx_main.patchx.util.ComProtocol;
import com.patchx_main.patchx.util.SpHelper;
import com.patchx_main.patchx.voice.WavFeeder;
import com.wcy.testserial.FirmwareUpgradeManager;

import org.greenrobot.eventbus.EventBus;

import java.io.File;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.util.Enumeration;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

public class DeviceManager {
    private static final String TAG = "DeviceManager";
    private SmdtManagerNew smdtManagerNew;
    private Context mContext;
    private static volatile DeviceManager INSTANCE;

    public static DeviceManager getInstance(Context context){
        if (INSTANCE == null) {
            synchronized (DeviceManager.class) {
                if (INSTANCE == null) {
                    INSTANCE = new DeviceManager(context.getApplicationContext()); // 用全局 context
                }
            }
        }
        return INSTANCE;
    }

    // 无参方法（仅供初始化后调用）
    public static DeviceManager getInstance() {
        if (INSTANCE == null) {
            throw new IllegalStateException("DeviceManager 未初始化，请先调用 getInstance(context)");
        }
        return INSTANCE;
    }

    private DeviceManager(Context context) {
        this.mContext = context;
        smdtManagerNew = SmdtManagerNew.getInstance(context);
    }

    public void initDeviceInfoValue(){
        String firstInit = SpHelper.getInstance(mContext).getString("firstInit",null);
        if (firstInit == null){
            SpHelper.getInstance(mContext).putBoolean("touchSwitch",true);
            SpHelper.getInstance(mContext).putBoolean("touchWake",true);
        }
    }

    public void startCheckDevice() {
        try {
            new Thread(() -> {
                //摄像头
                boolean hasCamera = isCameraAvailable(mContext, 0);
                if (hasCamera) {
                    UnifiedRobotState.i().setCamera("ok");
                }else{
                    EventBus.getDefault().post(new CheckEvent("fail",""));
                }
                //CPU
                String cpuInfo = smdtManagerNew.info_getCpuTemperature();
                UnifiedRobotState.i().setCpuTemperature(cpuInfo);
                //wifi信号
                int wifiSignal = smdtManagerNew.net_getWifiRssi(5);
                UnifiedRobotState.i().setWifiSignal(wifiSignal);
                //app版本号
                String appVersion = this.getVersionName(mContext);
                UnifiedRobotState.i().setAppVersion(appVersion);
                //系统版本号
                String sysVersion = smdtManagerNew.info_getSoftwareVersion();
                UnifiedRobotState.i().setSystemVersion(sysVersion);
                //亮度
                int brightness = smdtManagerNew.disp_getLcdBackLight(0);
                UnifiedRobotState.i().setBrightness(brightness);
                SpHelper.getInstance(mContext).putInt("currentBrightness",brightness);
                //音量
                int volume = smdtManagerNew.sys_getVolume();
                UnifiedRobotState.i().setVolume(volume);
                SpHelper.getInstance(mContext).putInt("currentVolume",volume);
                //机械臂版本号
                UnifiedRobotState.i().setFirmwareVersion("1.0.0");
                //电量
                UnifiedRobotState.i().setBatteryPct(72);
                Log.d(TAG, "检测完成");
            }).start();
        }catch (Exception e){
            e.printStackTrace();
        }

    }

    //更新设备信息
    public void updateDeviceInfo(){
        //CPU
        String cpuInfo = smdtManagerNew.info_getCpuTemperature();
        UnifiedRobotState.i().setCpuTemperature(cpuInfo);
        //wifi信号
        int wifiSignal = smdtManagerNew.net_getWifiRssi(5);
        UnifiedRobotState.i().setWifiSignal(wifiSignal);
        // IP地址
        UnifiedRobotState.i().setIPInfo(DeviceManager.getInstance().getNetWorkInf());
    }
    /**
     * 获取当前应用版本名
     */
    public String getVersionName(Context context) {
        try {
            PackageManager pm = context.getPackageManager();
            PackageInfo pi = pm.getPackageInfo(context.getPackageName(), 0);
            return pi.versionName;
        } catch (PackageManager.NameNotFoundException e) {
            e.printStackTrace();
        }
        return "";
    }



    public boolean isCameraAvailable(Context ctx, int timeoutSec) {
        CameraManager cm = (CameraManager) ctx.getSystemService(Context.CAMERA_SERVICE);
        try {
            String[] ids = cm.getCameraIdList();
            if (ids == null || ids.length == 0) return false;

            String cameraId = ids[0];
            HandlerThread ht = new HandlerThread("cam_check");
            ht.start();
            Handler handler = new Handler(ht.getLooper());
            CountDownLatch latch = new CountDownLatch(1);
            final boolean[] ok = {false};

            cm.openCamera(cameraId, new CameraDevice.StateCallback() {
                @Override
                public void onOpened(CameraDevice camera) {
                    ok[0] = true;
                    camera.close();
                    latch.countDown();
                }
                @Override
                public void onDisconnected(CameraDevice camera) {
                    camera.close();
                    latch.countDown();
                }
                @Override
                public void onError(CameraDevice camera, int error) {
                    if (camera != null) camera.close();
                    latch.countDown();
                }
            }, handler);

            latch.await(timeoutSec, TimeUnit.SECONDS);

            // 注意：不要立刻 quit
            handler.post(ht::quitSafely);

            return ok[0];
        } catch (CameraAccessException | SecurityException | InterruptedException e) {
            e.printStackTrace();
            return false;
        }
    }
    public void sendMqttInfo(String json){
        try{
//            Log.d(TAG,"===测试==sendMqttInfo===json==="+json);
            ControllerEntity controllerEntity = new Gson().fromJson(json,ControllerEntity.class);
            switch (controllerEntity.type){
                case "test":
                    testSendDeviceCommand(controllerEntity.payload.value);
//                    String info =  FirmwareUpgradeManager.i().buildAngleString(controllerEntity.payload.value);
                    break;
                case "testEmote":
                        EmoteMessage emoteMsg = new EmoteMessage(1, controllerEntity.payload.value.toString());
                        String emoteJson = emoteMsg.toJson();
                        UnityManager.getInstance().send("PhotoManager", "OnPatchMessage", emoteJson);
                    break;
                case "touchWakeUpSwitch":
                    SpHelper.getInstance(mContext).putBoolean("touchWakeUpSwitch", controllerEntity.payload.value == 0);
                    UnifiedRobotState.i().setTouchSwitch(controllerEntity.payload.value);
                    break;
                case "volume":
                    int setVolume =  smdtManagerNew.sys_setVolume(controllerEntity.payload.value);
                    if (setVolume == 0){
                        UnifiedRobotState.i().setVolume(controllerEntity.payload.value);
                        WavFeeder.tryPlayWavIfIdleFromAssets(mContext, "dingdong.wav");
                        SpHelper.getInstance(mContext).putInt("currentVolume",controllerEntity.payload.value);
                    }
                    break;
                case "brightness":
                    int setBrightnessR = smdtManagerNew.disp_setLcdBackLight(0,controllerEntity.payload.value,0,true);
                    if (setBrightnessR == 0){
                        UnifiedRobotState.i().setBrightness(controllerEntity.payload.value);
                        SpHelper.getInstance(mContext).putInt("currentBrightness",controllerEntity.payload.value);
                    }
                    break;
                case "unbind":
                    sendSystemUnBind();
                    break;
                case "divination":
                    //算命
                    if (controllerEntity.payload.flag == 1){
                        EventBus.getDefault().post(new SocketEvent("listen_mode","start","fortune"));
                    }else{
                        EventBus.getDefault().post(new SocketEvent("listen_mode","end",""));
                    }
                    break;
                case "decodeDreams":
                    //解梦
                    if (controllerEntity.payload.flag == 1){
                        EventBus.getDefault().post(new SocketEvent("listen_mode","start","dream"));
                    }else{
                        EventBus.getDefault().post(new SocketEvent("listen_mode","end",""));
                    }
                    break;
                case "appUpdate":
//                    MqttManager.getInstance().handleUpdateCommand(controllerEntity);
                    break;
                case "firmwareUpdate":
//                    MqttManager.getInstance().handleUpdateCommand(controllerEntity);
                    break;
                case "factoryReset":
                    //恢复出厂设置
                    smdtManagerNew.sys_rebootRecovery();
                    break;
                case "display":
                    setDisplayRotation(controllerEntity.payload.value);
                case "lcdback":
                   setLcdBackLight(controllerEntity.payload.value);
                case "backenable":
                    setLcdBackLightEnable(controllerEntity.payload.value == 1);
                default:
                    break;
            }
        }catch (Exception e){
            e.printStackTrace();
        }
    }
    public String getIpInfo(){
        try {
            String ipInfo = SpHelper.getInstance(mContext).getString("ipInfo","");
            return ipInfo;
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }


    public String getNetWorkInf(){
        try {
            for (Enumeration<NetworkInterface> en = NetworkInterface.getNetworkInterfaces(); en.hasMoreElements();) {
                NetworkInterface intf = en.nextElement();
                for (Enumeration<InetAddress> enumIpAddr = intf.getInetAddresses(); enumIpAddr.hasMoreElements();) {
                    InetAddress inetAddress = enumIpAddr.nextElement();
                    if (!inetAddress.isLoopbackAddress() && inetAddress instanceof Inet4Address) {
                        String ip = inetAddress.getHostAddress();
                        // 过滤掉非局域网地址
                        if (ip.startsWith("192.") || ip.startsWith("10.") || ip.startsWith("172.")) {
                            return ip;
                        }
                    }
                }
            }
        } catch (Exception ex) {
            ex.printStackTrace();
        }
        return null;
    }
    public void updateMainApp(File apk) {
        Uri uri = FileProvider.getUriForFile(
                mContext,
                mContext.getPackageName() + ".fileprovider",
                apk
        );
        Intent intent = new Intent("com.patchx_sys.ACTION_INSTALL_APK")
                .setComponent(new ComponentName(
                        "com.patchx_sys",
                        "com.patchx_sys.receiver.InstallReceiver"))
                .setData(uri)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);

        intent.putExtra("fileName", apk.getName());
        mContext.grantUriPermission("com.patchx_sys", uri,
                Intent.FLAG_GRANT_READ_URI_PERMISSION);

        mContext.sendBroadcast(intent);
    }

    public void sendSystemUnBind() {
        Intent intent = new Intent("com.patchx_sys.ACTION_UNBIND")
                .setComponent(new ComponentName(
                        "com.patchx_sys",
                        "com.patchx_sys.receiver.UnBindReceiver"));
        intent.putExtra("needReSetWifi", "true");
        mContext.sendBroadcast(intent);
    }

    public void sendDeviceCommand(int tag){
        try {
            String info = ComProtocol.getFrameHexForCmd(tag);
            if (tag == 21 || tag == 22 || tag == 23 || tag == 24) {
                sendDeviceAction(info,MIN_INTERVAL_LONG_MS);
            }else{
                sendDeviceAction(info,MIN_INTERVAL_MS);
            }
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }
    public void testSendDeviceCommand(int tag){
        String info = ComProtocol.getFrameHexForCmd(tag);
        sendDeviceAction(info,MIN_INTERVAL_MS);
    }
    public void sendDeviceAngle(int angle){
        String info = FirmwareUpgradeManager.i().buildAngleString(angle);
        String uart = UART_PATH;
        int baudrate = 115200;
        int databits = 8;
        int stopbits = 1;
        int parity = 0;
        int flow_ctrl = 0;
        smdtManagerNew.dev_closeUart(uart);
        int result = smdtManagerNew.dev_openUart(uart, baudrate, databits, stopbits, parity, flow_ctrl);
        if (result == RET_API_OK) {
            // Payload logging omitted from the public build.
            int sendResult = smdtManagerNew.dev_sendUart(uart, info, true);
            smdtManagerNew.dev_closeUart(uart);
            // 打开成功后保持接收串口数据
            smdtManagerNew.dev_receiveUart(uart, new SmdtManagerNew.DataCallback() {
                @Override
                public void onDataReceive(byte[] buffer, int size) throws RemoteException {
                    if (buffer != null) {
                        Log.d(TAG, "UART 接收数据 size=" + size);
                    }
                }
            });
        }
    }
    // 类里加：最后下发时间戳（毫秒）
    private long lastSendTime = 0L;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private static final long CHECK_INTERVAL = 60_000; // 1分钟
    private static final String UART_PATH = "/dev/ttySMT0";

    // 最小下发间隔
    private static final long MIN_INTERVAL_MS = 6000; // 6秒

    private static final long MIN_INTERVAL_LONG_MS = 8000; // 6秒

    // 启动定时器，在 onCreate 或初始化时调用一次
    private void startCheckTask() {
        handler.postDelayed(new Runnable() {
            @Override
            public void run() {
                long now = System.currentTimeMillis();
                if (now - lastSendTime >= CHECK_INTERVAL) {
                    // 超过1分钟没发 -> 发复位指令 14
                    sendDeviceCommand(14);
                }
                handler.postDelayed(this, CHECK_INTERVAL);
            }
        }, CHECK_INTERVAL);
    }

    // 发送动作（加了 6 秒间隔限制）
    public void sendDeviceAction(String info, long time) {
        long now = System.currentTimeMillis();
        if (now - lastSendTime < time) {
            // Payload logging omitted from the public build.
            return;
        }
        String uart = UART_PATH;
        int baudrate = 115200;
        int databits = 8;
        int stopbits = 1;
        int parity = 0;
        int flow_ctrl = 0;

        smdtManagerNew.dev_closeUart(uart);
        int result = smdtManagerNew.dev_openUart(uart, baudrate, databits, stopbits, parity, flow_ctrl);
        if (result == RET_API_OK) {
            // Payload logging omitted from the public build.
            int sendResult = smdtManagerNew.dev_sendUart(uart, info, true);

            // 更新下发时间戳（成功或失败都更新，避免频繁尝试）
            lastSendTime = now;
            smdtManagerNew.dev_closeUart(uart);
            // 打开成功后保持接收串口数据
            smdtManagerNew.dev_receiveUart(uart, new SmdtManagerNew.DataCallback() {
                @Override
                public void onDataReceive(byte[] buffer, int size) throws RemoteException {
                    if (buffer != null) {
                        Log.d(TAG, "UART 接收数据 size=" + size);
                    }
                }
            });
        }
    }
    private static int clamp(int v, int lo, int hi) {
        return v < lo ? lo : (v > hi ? hi : v);
    }

    public void setVolume(int value) {
        try{
            Log.d(TAG,"测试====setVolume===传入的value===" + value);

            int maxVolume = smdtManagerNew.sys_getVolumeMaxMin("max");
            int minVolume = smdtManagerNew.sys_getVolumeMaxMin("min");
            if (maxVolume < minVolume) { int t = maxVolume; maxVolume = minVolume; minVolume = t; }

            int current   = smdtManagerNew.sys_getVolume();
            int setVal;

            if (value == 0) {
                setVal = minVolume;
            } else if (value == 100) {
                setVal = maxVolume;
            } else {
                int p = Math.max(-100, Math.min(100, value));
                int delta = (int) Math.round(maxVolume * (Math.abs(p) / 100.0));
                if (delta > 0) delta = Math.max(1, delta);
                int target = (p >= 0) ? (current + delta) : (current - delta);
                setVal = clamp(target, minVolume, maxVolume);
            }

            Log.d(TAG,"测试====setVolume===setValue==="+setVal+"====currentLight==="+current+"==maxVolume=="+maxVolume+"===minVolume=="+minVolume);
            int result =  smdtManagerNew.sys_setVolume(setVal);
            if (result == 0){
                SpHelper.getInstance(mContext).putInt("currentVolume",setVal);
                UnifiedRobotState.i().setVolume(setVal);
            }
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    public void setBrightness(int value) {
        try{
            Log.d(TAG,"测试====setBrightness===传入的value===" + value);

            int maxLight = smdtManagerNew.disp_getLcdBackLightMaxMin(0, "max");
            int minLight = smdtManagerNew.disp_getLcdBackLightMaxMin(0, "min");
            if (maxLight < minLight) { int t = maxLight; maxLight = minLight; minLight = t; }

            int current  = smdtManagerNew.disp_getLcdBackLight(0);
            int setVal;

            if (value == 0) {
                setVal = minLight;
            } else if (value == 100) {
                setVal = maxLight;
            } else {
                int p = Math.max(-100, Math.min(100, value));
                int delta = (int) Math.round(maxLight * (Math.abs(p) / 100.0));
                if (delta > 0) delta = Math.max(1, delta);
                int target = (p >= 0) ? (current + delta) : (current - delta);
                setVal = clamp(target, minLight, maxLight);
            }

            Log.d(TAG,"测试====setBrightness===setValue==="+setVal+"====currentLight==="+current+"==maxLight=="+maxLight+"===minLight=="+minLight);
            int result =  smdtManagerNew.disp_setLcdBackLight(0, setVal, 0, true);
            if (result == 0){
                SpHelper.getInstance(mContext).putInt("currentBrightness",setVal);
                UnifiedRobotState.i().setBrightness(setVal);
            }
        }catch (Exception e){
            e.printStackTrace();
        }

    }
    public void setDisplayRotation(int degree){
        try {
            smdtManagerNew.disp_setDisplayRotation(0,degree);
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    public void setLcdBackLight(int setValue) {
        try {
            int openResult0 = smdtManagerNew.disp_setLcdBackLightEnable(0, true);
            if (openResult0 != 0) return;
            final int currentValue = smdtManagerNew.disp_getLcdBackLight(0);
            if (currentValue == setValue) return;
            final int steps = 5;
            final long stepDelayMs = 80;
            final float delta = (setValue - currentValue) / (float) steps;
            android.os.Handler h = new android.os.Handler(android.os.Looper.getMainLooper());
            final int[] i = {0};
            Runnable r = new Runnable() {
                @Override public void run() {
                    i[0]++;
                    int intermediate = Math.round(currentValue + delta * i[0]);
                    smdtManagerNew.disp_setLcdBackLight(0, intermediate, 0, true);
                    if (i[0] < steps) {
                        h.postDelayed(this, stepDelayMs);
                    }
                }
            };
            h.post(r);
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }
    public void setLcdBackLightWithValue(int setValue){
        try{
            smdtManagerNew.disp_setLcdBackLight(0, setValue, 0, true);
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    public void setLcdBackLightEnable(boolean enable){
        try {
            smdtManagerNew.disp_setLcdBackLightEnable(0,enable);
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }
}
