package com.patchx_main.patchx.voice;

import android.content.Context;
import android.content.res.AssetFileDescriptor;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import com.patchx_main.ability.tts.PlayerManager;
import com.patchx_main.caeLib.VoiceEngineMgr;
import com.patchx_main.caeLib.bean.WakeInfo;
import com.patchx_main.caeLib.engine.IEngineCallBack;
import com.patchx_main.patchx.event.SocketEvent;
import com.patchx_main.patchx.event.WakeUpEvent;
//import com.patchx_main.patchx.manage.UnityManager;
import com.patchx_main.patchx.manage.DeviceManager;
import com.patchx_main.patchx.manage.UnityManager;
import com.patchx_main.patchx.manage.WakeAudioStreamManager;
import com.patchx_main.patchx.manage.WebSocketManager;
import com.patchx_main.patchx.unity.EmoteMessage;
import com.patchx_main.patchx.util.InactivityScreenOffWatcher;
//import com.patchx_main.patchx.unity.EmoteMessage;

import org.greenrobot.eventbus.EventBus;

import java.io.IOException;

public class WakeUpController {
    private static final String TAG = "WakeUpController";

    private final Context context;
    private VoiceEngineMgr voiceEngineMgr;
    private WebSocketManager webSocketManager;
    private WakeAudioStreamManager wakeAudioManager;

    private final String[] wakeTip = new String[]{"zai.wav", "help.wav"};

    private void playWakeTip() {
        try {
            AssetFileDescriptor open = this.context.getAssets().openFd(wakeTip[(int) (Math.random() * wakeTip.length)]);
            PlayerManager.getInstance().play(open, (state, msg) -> {
            });
        } catch (IOException e) {
            e.printStackTrace();
        }
    }

    public WakeUpController(Context ctx, WebSocketManager wsManager) {
        this.context = ctx;
        this.webSocketManager = wsManager;
        this.wakeAudioManager = new WakeAudioStreamManager(wsManager);
    }

    /** 启动唤醒引擎 */
    public void start() {
        try{
            voiceEngineMgr = VoiceEngineMgr.getInstance();
            voiceEngineMgr.startWork(new IEngineCallBack() {
                @Override
                public void onWakeup(WakeInfo wakeInfo) {
                    //playWakeTip();
                    if (webSocketManager.allowWakeUp){
                        Log.i(TAG, "===测试===测试==== 唤醒成功 ====wakeInfo=="+ wakeInfo.toString());
                        if (!webSocketManager.isConnected()) {
                            Log.w(TAG, "测试=====WebSocket未连接，唤醒后建立连接...");
                            webSocketManager.connect();
                        }else{
                            Log.w(TAG, "测试=====WebSocket已经连接");
                            //webSocketManager.isReadyToSend = false;
                            webSocketManager.sendAbortMessage();
                        }
                        EventBus.getDefault().post(new WakeUpEvent("success", "", wakeInfo.getAngle()));
                        int angleInfo = 0;
                        if (wakeInfo.getBeam() == 0){
                            if (wakeInfo.getAngle() >=0 && wakeInfo.getAngle() <= 60){
                                angleInfo = wakeInfo.getAngle();
                            }else{
                                angleInfo = 30;
                            }
                        } else if (wakeInfo.getBeam() == 1) {
                            if (wakeInfo.getAngle() >60 && wakeInfo.getAngle() <= 120){
                                angleInfo = wakeInfo.getAngle();
                            }else{
                                angleInfo = 90;
                            }
                        }else if (wakeInfo.getBeam() == 2) {
                            if (wakeInfo.getAngle() >120 && wakeInfo.getAngle() <= 180){
                                angleInfo = wakeInfo.getAngle();
                            }else{
                                angleInfo = 150;
                            }
                        }
                        DeviceManager.getInstance().sendDeviceAngle(angleInfo);
                        System.out.println("测试===发送角度===="+angleInfo);
                        DeviceManager.getInstance().setLcdBackLightEnable(true);
                        if (webSocketManager.firstWakeUp){
                            webSocketManager.firstWakeUp = false;
                            new Handler(Looper.getMainLooper()).postDelayed(() -> {
                                DeviceManager.getInstance().sendDeviceCommand(1);
                                WavFeeder.tryPlayWavIfIdleFromAssets(context.getApplicationContext(), "haqian.wav");
                            }, 300);

                            EmoteMessage msg = new EmoteMessage(1, "1");
                            String json = msg.toJson();
                            UnityManager.getInstance().send("PhotoManager", "OnPatchMessage", json);
                        }else{
                            new Handler(Looper.getMainLooper()).postDelayed(() -> {
                                DeviceManager.getInstance().sendDeviceCommand(25);
                            }, 300);

                        }

                        webSocketManager.doUpdateUserInfo();
                        webSocketManager.needCheckSpeaker = true;
                        // 刷新
                        InactivityScreenOffWatcher.i().refresh();
                    }
                }

                @Override
                public void onAudioCallback(byte[] bytes, int len) {
                    // ✅ 将原始帧交给 WakeAudioStreamManager 发送
                    //Log.w(TAG, "测试=====onAudioCallback==len:"+len);
                    if (webSocketManager.isReadyToSend.get()){
                        wakeAudioManager.onAudioFrame(bytes, len);
                    }
                }
            });
//            try {
//                PcmSaveMgr.getInstance().setSavePcm(true);
//            } catch (FileNotFoundException e) {
//                throw new RuntimeException(e);
//            }
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    public void release() {
        if (voiceEngineMgr != null) {
            voiceEngineMgr.stopWork();
            voiceEngineMgr = null;
        }
        if (webSocketManager != null) {
            webSocketManager.release_resource();
            webSocketManager = null;
        }
        wakeAudioManager.release();
    }

    public void sendMessage(SocketEvent event){
        if (webSocketManager.isConnected()){
            webSocketManager.sendSocketMessage(event);
        }
    }
}
