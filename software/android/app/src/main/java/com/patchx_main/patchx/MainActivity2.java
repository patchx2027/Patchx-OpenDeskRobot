package com.patchx_main.patchx;

import com.patchx_main.BuildConfig;

import android.graphics.PixelFormat;
import android.os.Bundle;

import android.os.Handler;
import android.os.Looper;
import android.text.TextUtils;
import android.util.Log;
import android.view.Choreographer;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.lifecycle.Lifecycle;
import androidx.lifecycle.LifecycleOwner;
import androidx.lifecycle.LifecycleRegistry;

import com.AudioProccessing.SpeakerRecognizer;
import com.VideoProcess.ImageProcessing;
import com.VideoProcess.ModelConfig;
import com.patchx.patchface.FaceDb;
import com.patchx.patchface.FaceEngine;
import com.patchx_main.R;
import com.patchx_main.patchx.event.AudioLevelEvent;
import com.patchx_main.patchx.event.CheckEvent;
import com.patchx_main.patchx.event.SocketEvent;
import com.patchx_main.patchx.event.WakeUpEvent;
import com.patchx_main.patchx.manage.CheckManager;

import com.patchx_main.patchx.manage.FaceRecognitionManager;
import com.patchx_main.patchx.manage.ImageSaver;

import com.patchx_main.patchx.manage.DeviceManager;
import com.patchx_main.patchx.manage.MqttManager;

import com.patchx_main.patchx.manage.UnityManager;
import com.patchx_main.patchx.manage.WebSocketManager;
import com.patchx_main.patchx.mqtt.UnifiedRobotState;
import com.patchx_main.patchx.util.InactivityScreenOffWatcher;
import com.patchx_main.patchx.util.IpUtils;
import com.patchx_main.patchx.util.SpHelper;
import com.patchx_main.patchx.voice.AudioFrameQueue;
import com.patchx_main.patchx.voice.VadResultSaver;
import com.patchx_main.patchx.voice.WakeUpController;

import org.greenrobot.eventbus.EventBus;
import org.greenrobot.eventbus.Subscribe;
import org.greenrobot.eventbus.ThreadMode;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;


//com.unity3d.player.UnityPlayerActivity
public class MainActivity2 extends com.unity3d.player.UnityPlayerActivity  implements LifecycleOwner {
    // 权限请求码
    private  static final  String TAG="MainActivity2";
    private final LifecycleRegistry lifecycleRegistry = new LifecycleRegistry(this);
//    private TextView checkStatus;
//    private TextView wakeUpStatus;
//
    private final Handler loadingHandler = new Handler(Looper.getMainLooper());
    private Runnable loadingRunnable;
    private TextView loadingText;

    WakeUpController wakeController;
    public  static  String _exPath="";
    public  static VadResultSaver  wavsaver;
    public static ImageSaver imgsaver;

    private View splashView;
    private WindowManager wm;
    private ScheduledExecutorService scheduledExecutorService;
    private FaceRecognitionManager manager;
    private FaceEngine faceEngine;
    private FaceDb faceDb;

    @NonNull
    @Override
    public Lifecycle getLifecycle() {
        return lifecycleRegistry;
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        splashView  = LayoutInflater.from(this).inflate(R.layout.view_unity_splash, null);
        wm  = (WindowManager) getSystemService(WINDOW_SERVICE);
        WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
                        | WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
                PixelFormat.TRANSLUCENT
        );
        lp.gravity = Gravity.CENTER;
        loadingText = splashView.findViewById(R.id.loadingText);
        Choreographer.getInstance().postFrameCallback(frameTimeNanos -> {
            wm.addView(splashView, lp);
            splashView.post(this::startLoadingDots);
        });
        View dimOverlay = splashView.findViewById(R.id.dimOverlay);
        dimOverlay.animate()
                .alpha(0f)
                .setDuration(4000)
                .setStartDelay(0)
                .start();
        String[] model_arr={
                "silero_vad.onnx",
                "ten-vad.onnx"
        };
        _exPath =  copyAssetToExStorage(model_arr[1]);
//        SpeakerRecognizer.getInstance().init(copyAssetToExStorage("speaker_model.onnx"));

//        wavsaver = new VadResultSaver(this);
//        imgsaver = new ImageSaver(this);
//        setContentView(R.layout.activity_main);
//        initUI();
        //初始化日志系统
        com.Loogger.LogManager.getInstance(this).init();
        //初始化设备自检项
        UnifiedRobotState.i().initIdentity(BuildConfig.PATCHX_USER_ID, BuildConfig.PATCHX_DEVICE_SN);
        //eventbus注册
        EventBus.getDefault().register(this);
        //语音检查并开启唤醒
        CheckManager.getInstance().startCheckTaskAction(this);
        //其他设备初始化值
        DeviceManager.getInstance(this).initDeviceInfoValue();
        //其他设备自检
        DeviceManager.getInstance().startCheckDevice();
        // 开启默认屏幕亮度
        DeviceManager.getInstance().setLcdBackLightWithValue(SpHelper.getInstance(this).getInt("currentBrightness",128));
        //建立mqtt连接
        MqttManager.getInstance().startMqttConnect(this);
        DeviceManager.getInstance().setLcdBackLightEnable(true);
        IpUtils.getPublicIpAsync(new okhttp3.Callback() {
            @Override public void onFailure(okhttp3.Call call, java.io.IOException e) {
            }
            @Override public void onResponse(okhttp3.Call call, okhttp3.Response response) throws java.io.IOException {
                if (response.isSuccessful()) {
                    String ip = response.body() != null ? response.body().string().trim() : null;
                    SpHelper.getInstance(MainActivity2.this).putString("ipInfo",ip);
                }
                if (response.body() != null) response.close();
            }
        });



        String msg = DeviceManager.getInstance().getNetWorkInf();
        loadingText.setText(msg);
        if (!TextUtils.isEmpty(msg)) {
            Toast.makeText(this, msg, Toast.LENGTH_LONG).show();
        } else {
            Toast.makeText(this, "网络信息为空", Toast.LENGTH_LONG).show();
        }
//
//        EmoteMessage emoteMsg = new EmoteMessage(1, "18");
//        String json = emoteMsg.toJson();
//        UnityManager.getInstance().send("PhotoManager", "OnPatchMessage", json);
        startZYAction();
//        startMyAction();
    }

    private void startZYAction(){
        ModelConfig modelConfig = new ModelConfig(this);
        ImageProcessing.getInstance().init(modelConfig.get_Model_RootPath());
        boolean success = ImageProcessing.getInstance().openCamera(1);
        if (success) {
            Log.d(TAG,"camera init ok");
            startTask();
        } else {
            Log.e(TAG,"相机启动失败");
        }
    }

    private void startTask(){
        scheduledExecutorService = Executors.newSingleThreadScheduledExecutor();
        scheduledExecutorService.scheduleWithFixedDelay(new Runnable() {
            @Override
            public void run() {
                String faceInfo =   ImageProcessing.getInstance().GetFaceInfo();
                // Payload logging omitted from the public build.
            }
        },0,1000, TimeUnit.MILLISECONDS);
    }

    private void stopTask(){
        if (scheduledExecutorService != null && !scheduledExecutorService.isShutdown()){
            scheduledExecutorService.shutdown();
        }
    }


    private void startMyAction(){
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_CREATE);
        faceDb = new FaceDb(this);
        faceEngine = new FaceEngine();

        boolean ok = faceEngine.init(
                getAssets(),
                "models/RetinaFace_mobile320_fp32.rknn",
                "models/MobileFaceNet_fp32.rknn"
        );
        if (!ok) {
            Toast.makeText(this, "模型初始化失败", Toast.LENGTH_LONG).show();
        }else{
            manager = new FaceRecognitionManager(this,this,faceEngine,faceDb,true);
            manager.setProcessGapMs(1000);
            manager.setListener(new FaceRecognitionManager.OnRecognitionListener() {
                @Override public void onFaceDirection(String dir, float offX) {
                    Log.i(" 测试====SilentFace", "方向: " + dir);
                }
                @Override public void onFeature(float[] feat) {
                    Log.i(" 测试====SilentFace", "feat len=" + feat.length);
                }
                @Override public void onRecognizedLog(String msg) {
                    // Recognition content is not written to the application log.
                }
                @Override public void onNoFace() {
                    Log.d(" 测试====SilentFace", "未检测到人脸");
                }
                @Override public void onError(Throwable t) {
                    Log.e(" 测试====SilentFace", "error", t);
                }
            });
            manager.start();
        }
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        DeviceManager.getInstance().setLcdBackLightEnable(true);
        return true;
    }

    @Subscribe(threadMode = ThreadMode.MAIN)
    public void onCheckEvent(CheckEvent event) {
        if (event.result.equals("success")){
            AudioFrameQueue audioFrameQueue  = new AudioFrameQueue();
            WebSocketManager wsManager = new WebSocketManager(this,audioFrameQueue);
            wakeController = new WakeUpController(this, wsManager);
            wakeController.start();
            InactivityScreenOffWatcher.i().start();
        }else{
//            System.out.println("测试===自检失败");
        }


    }

    @Subscribe(threadMode = ThreadMode.MAIN)
    public void onAudioLevelEvent(AudioLevelEvent event){
//        rippleLevelView.setLevel(event.level);
    }

    @Subscribe(threadMode =  ThreadMode.MAIN)
    public void onWakeUpEvent(WakeUpEvent event){
        // Payload logging omitted from the public build.
//        wakeUpStatus.setText(event.result);
        if (event.result.equals("success")){
        }
    }

    @Subscribe(threadMode = ThreadMode.MAIN)
    public void onSocketEvent(SocketEvent event){
        if (wakeController != null){
            wakeController.sendMessage(event);
        }

        Log.d(TAG,"wake up");

        //imgsaver.saveJpgToPrivateDir(improcessor.takePicture(""),"llll");


    }


    private void quitAactivity(){
        Log.d(TAG,"quitAactivity");

        if(wakeController!=null)
            wakeController.release();
        finish();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();

        ImageProcessing.getInstance().closeCamera();
        ImageProcessing.getInstance().uninit();
        SpeakerRecognizer.getInstance().release();

        EventBus.getDefault().unregister(this);
        //停止唤醒

        //WakeUpManager.getInstance().stopWakeUpAction();
        quitAactivity();

        //停止mqtt
        MqttManager.getInstance().stopAliMqttClient();
        if (wakeController != null){
            wakeController.release();
        }
        InactivityScreenOffWatcher.i().stop();

        stopLoadingDots();
        loadingText = null;

        stopTask();

        if (manager != null){
            manager.stop();
        }
        if (faceEngine != null){
            faceEngine.release();
        }
    }


    private String copyAssetToExStorage(String assetFileName) {
        try {
            InputStream is = getAssets().open(assetFileName);
            File outputFile = new File(getExternalFilesDir(null), assetFileName); // 内部存储路径：/data/data/包名/files/
            OutputStream os = new FileOutputStream(outputFile);

            byte[] buffer = new byte[1024];
            int length=0;
            while ((length = is.read(buffer)) > 0) {
                os.write(buffer, 0, length);
            }

            os.flush();
            os.close();
            is.close();

            //return getExternalFilesDir(null).toString();
            return outputFile.getAbsolutePath();
        } catch (IOException e) {
            e.printStackTrace();
            return null;
        }
    }


    private void removeSplashOverlay() {
        ViewGroup content = findViewById(android.R.id.content);
        if (wm != null){
            wm.removeView(splashView);
            wm = null;
        }
        if (splashView != null && splashView.getParent() == content) {
            content.removeView(splashView);
        }
        splashView = null;
        stopLoadingDots();
        loadingText = null;
    }
    @SuppressWarnings("unused")
    public void onUnityReady() {
        runOnUiThread(new Runnable() {
            @Override
            public void run() {
//                UnityManager.getInstance().sendSpeed("PhotoManager", "OnPatchSpeed", "1.0");
                UnityManager.getInstance().sendSpeed("PhotoManager", "OnMouthSpeed", "0.60");
                removeSplashOverlay();
            }
        });
    }
    private void startLoadingDots() {
        stopLoadingDots(); // 避免重复
        loadingRunnable = new Runnable() {
            int dot = 0;
            @Override public void run() {
                if (loadingText != null) {
                    dot = (dot + 1) % 4;
                    StringBuilder sb = new StringBuilder("Loading");
                    for (int i = 0; i < dot; i++) sb.append('.');
                    loadingText.setText(sb.toString());
                }
                loadingHandler.postDelayed(this, 500);
            }
        };
        loadingHandler.post(loadingRunnable);
    }
    private void stopLoadingDots() {
        if (loadingRunnable != null) {
            loadingHandler.removeCallbacks(loadingRunnable);
            loadingRunnable = null;
        }
    }

    // 当页面被覆盖/不可见时停止
    @Override protected void onPause() {
        super.onPause();
        stopLoadingDots();
    }

    // 如果回到前台还需要显示，再启动
    @Override protected void onResume() {
        super.onResume();
        startLoadingDots();
    }
}
