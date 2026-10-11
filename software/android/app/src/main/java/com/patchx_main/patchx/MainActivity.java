//package com.patchx_main.patchx;
//
//import android.os.Bundle;
//
//import android.util.Log;
//import android.widget.TextView;
//
//import androidx.appcompat.app.AppCompatActivity;
//
//import com.AudioProccessing.SpeekerID;
//import com.VideoProcess.ImageProcessing;
//import com.VideoProcess.ModelConfig;
//import com.patchx_main.R;
//import com.patchx_main.patchx.event.AudioLevelEvent;
//import com.patchx_main.patchx.event.CheckEvent;
//import com.patchx_main.patchx.event.SocketEvent;
//import com.patchx_main.patchx.event.WakeUpEvent;
//import com.patchx_main.patchx.manage.CheckManager;
//
//import com.patchx_main.patchx.manage.ImageSaver;
//
//import com.patchx_main.patchx.manage.DeviceManager;
//import com.patchx_main.patchx.manage.MqttManager;
//
//import com.patchx_main.patchx.manage.WebSocketManager;
//import com.patchx_main.patchx.mqtt.UnifiedRobotState;
//import com.patchx_main.patchx.view.RippleLevelView;
//import com.patchx_main.patchx.voice.AudioFrameQueue;
//import com.patchx_main.patchx.voice.VadResultSaver;
//import com.patchx_main.patchx.voice.WakeUpController;
//
//import org.greenrobot.eventbus.EventBus;
//import org.greenrobot.eventbus.Subscribe;
//import org.greenrobot.eventbus.ThreadMode;
//
//import java.io.File;
//import java.io.FileOutputStream;
//import java.io.IOException;
//import java.io.InputStream;
//import java.io.OutputStream;
//
//
//public class MainActivity extends AppCompatActivity   {
//    // 权限请求码
//    private  static final  String TAG="MainActivity";
//    private TextView checkStatus;
//    private TextView wakeUpStatus;
//
//
//    WakeUpController wakeController;
//    public  static  String _exPath="";
//    public  static VadResultSaver  wavsaver;
//    public static ImageSaver imgsaver;
//
//
//    private TextView sttResult;
//    private TextView appVersion;
//    private RippleLevelView rippleLevelView;
//    private WakeUpController wakeUpController;
//
//
//
//
//    @Override
//    protected void onCreate(Bundle savedInstanceState) {
//        super.onCreate(savedInstanceState);
//
//
//
//
//
//        //_exPath =  copyAssetToExStorage("silero_vad.onnx");
//        String[] model_arr={
//                "silero_vad.onnx",
//                "ten-vad.onnx"
//        };
//        _exPath =  copyAssetToExStorage(model_arr[1]);
//
//        wavsaver = new VadResultSaver(this);
//        imgsaver = new ImageSaver(this);
//
//        setContentView(R.layout.activity_main);
//
//        initUI();
//        //初始化日志系统
//        com.Loogger.LogManager.getInstance(this).init();
//        //初始化设备自检项
//        //eventbus注册
//        EventBus.getDefault().register(this);
//        //语音检查并开启唤醒
//        CheckManager.getInstance().startCheckTaskAction(this);
//
//        //其他设备初始化值
//        DeviceManager.getInstance(this).initDeviceInfoValue();
//        //其他设备自检
//        DeviceManager.getInstance().startCheckDevice();
//        //建立mqtt连接
//        MqttManager.getInstance().startMqttConnect(this);
//        appVersion.setText(DeviceManager.getInstance().getVersionName(this));
//
//
//        //语音识别
//        _exPath =  copyAssetToExStorage("speaker_model.onnx");
//        SpeekerID.getInstance().init(_exPath);
//
//
//        ModelConfig modelConfig = new ModelConfig(this);
//
//        ImageProcessing.getInstance().init(modelConfig.get_Model_RootPath());
//
//        boolean success = ImageProcessing.getInstance().openCamera(1);
//        if (success) {
//            Log.d(TAG,"camera init ok");
//        } else {
//            Log.e(TAG,"相机启动失败");
//        }
//
//    }
//
//    private void initUI(){
//        appVersion = findViewById(R.id.appVersion);
//        checkStatus = findViewById(R.id.checkStatus);
//        wakeUpStatus = findViewById(R.id.wakeUpStatus);
//        rippleLevelView = findViewById(R.id.ripple);
//    }
//
//
//    @Subscribe(threadMode = ThreadMode.MAIN)
//    public void onCheckEvent(CheckEvent event) {
//        checkStatus.setText(event.result);
//        if (event.result.equals("success")){
//            AudioFrameQueue audioFrameQueue  = new AudioFrameQueue();
//            WebSocketManager wsManager = new WebSocketManager(this,audioFrameQueue);
//            wakeController = new WakeUpController(this, wsManager);
//            wakeController.start();
//
//        }
//
//
//    }
//
//    @Subscribe(threadMode = ThreadMode.MAIN)
//    public void onAudioLevelEvent(AudioLevelEvent event){
//        rippleLevelView.setLevel(event.level);
//    }
//
//    @Subscribe(threadMode =  ThreadMode.MAIN)
//    public void onWakeUpEvent(WakeUpEvent event){
//        Log.d(TAG,"====onWakeUpEvent===="+event.result);
//        wakeUpStatus.setText(event.result);
//        if (event.result.equals("success")){
//        }
//    }
//
//    @Subscribe(threadMode = ThreadMode.MAIN)
//    public void onSocketEvent(SocketEvent event){
//        if (wakeUpController != null){
//            wakeUpController.sendMessage(event);
//        }
//
//        Log.d(TAG,"wake up");
//
//        //imgsaver.saveJpgToPrivateDir(improcessor.takePicture(""),"llll");
//
//
//    }
//
//
//    private void quitAactivity(){
//        Log.d(TAG,"quitAactivity");
//
//        if(wakeController!=null)
//            wakeController.release();
//        finish();
//    }
//
//    @Override
//    protected void onDestroy() {
//        super.onDestroy();
//
//
//        ImageProcessing.getInstance().closeCamera();
//
//        EventBus.getDefault().unregister(this);
//        //停止唤醒
//
//        //WakeUpManager.getInstance().stopWakeUpAction();
//        quitAactivity();
//
//        //停止mqtt
//        MqttManager.getInstance().stopAliMqttClient();
//        if (wakeUpController != null){
//            wakeUpController.release();
//        }
//        SpeekerID.getInstance().uninit();
//    }
//
//
//    private String copyAssetToExStorage(String assetFileName) {
//        try {
//            InputStream is = getAssets().open(assetFileName);
//            File outputFile = new File(getExternalFilesDir(null), assetFileName); // 内部存储路径：/data/data/包名/files/
//            OutputStream os = new FileOutputStream(outputFile);
//
//            byte[] buffer = new byte[1024];
//            int length=0;
//            while ((length = is.read(buffer)) > 0) {
//                os.write(buffer, 0, length);
//            }
//
//            os.flush();
//            os.close();
//            is.close();
//
//            //return getExternalFilesDir(null).toString();
//            return outputFile.getAbsolutePath();
//        } catch (IOException e) {
//            e.printStackTrace();
//            return null;
//        }
//    }
//
//
//
//
//
//
//
//
//
//}
