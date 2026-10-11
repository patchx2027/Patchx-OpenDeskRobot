package com.patchx_main.patchx.manage;

import static com.theeasiestway.opus.Opus.OPUS_APPLICATION_VOIP;

import android.content.Context;
import com.patchx_main.BuildConfig;
import android.media.AudioTrack;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import com.VideoProcess.ImageProcessing;
import com.github.f4b6a3.uuid.UuidCreator;
import com.orhanobut.logger.Logger;
//import com.patchx_main.patchx.MainActivity;
//import com.patchx_main.patchx.MainActivity2;
import com.patchx_main.patchx.event.SocketEvent;
//import com.patchx_main.patchx.unity.EmoteScheduler;
import com.patchx_main.patchx.mqtt.UnifiedRobotState;
import com.patchx_main.patchx.unity.EmoteMessage;
import com.patchx_main.patchx.unity.EmoteScheduler;
import com.patchx_main.patchx.util.InactivityScreenOffWatcher;
import com.patchx_main.patchx.view.LipSyncAnimator;
import com.patchx_main.patchx.voice.AudioConfig;
import com.patchx_main.patchx.voice.AudioFrameQueue;
import com.patchx_main.patchx.voice.AudioLevelAnalyzer;
import com.patchx_main.patchx.voice.AudioPlayer;
import com.patchx_main.patchx.voice.Events;
import com.patchx_main.patchx.voice.TimerUtil;
import com.theeasiestway.opus.OpusDecoder;
import com.theeasiestway.opus.OpusEncoder;

import org.greenrobot.eventbus.EventBus;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.Base64;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;
import okio.ByteString;

public class WebSocketManager {
    private static final String TAG = "WebSocketManager";
    private static final String WS_URL = BuildConfig.PATCHX_WEBSOCKET_URL;
    private static final String ACCESS_TOKEN = BuildConfig.PATCHX_ACCESS_TOKEN;
    private static final String CLIENT_ID = BuildConfig.PATCHX_CLIENT_ID;
    private  static final String LISTEN_MODE="realtime";//manual  auto


    private Context context;
    private OkHttpClient client;
    private WebSocket webSocket;
    private String sessionId;

    private OpusEncoder opusEncoder;
    private OpusDecoder opusDecoder;

    private ExecutorService audioPlayExecutor = Executors.newSingleThreadExecutor();
    private ExecutorService sendExecutor = Executors.newSingleThreadExecutor();

    private volatile boolean isAllowedToSend = true;
    private volatile boolean isPlaying = false;

    private final AtomicBoolean tts_is_running = new AtomicBoolean(false);

    private TimerUtil timer_loop;
    private final AudioFrameQueue frameQueue;
    private final int frameSize;

    public AtomicBoolean isReadyToSend = new AtomicBoolean(false);
    private final EmoteScheduler emoteScheduler =new EmoteScheduler("PhotoManager", "OnPatchMessage");

    private boolean isDreamingOrFortune = false;
//    private String androidId;
    public boolean allowWakeUp = true;
    private final AudioLevelAnalyzer audioLevelAnalyzer = new AudioLevelAnalyzer();

    public boolean firstWakeUp = true;

    public boolean needCheckSpeaker = true;
    private LipSyncAnimator lipSyncAnimator;
    private boolean allowPlay = true;
    public WebSocketManager(Context context, AudioFrameQueue queue) {
        this.context = context;
        this.frameQueue = queue;
        this.frameSize = AudioConfig.FRAME_SIZE;
        this.opusEncoder = new OpusEncoder();
        this.opusDecoder = new OpusDecoder();
        opusEncoder.init(16000, 1, OPUS_APPLICATION_VOIP);
        opusDecoder.init(16000, 1);
        timer_loop = new TimerUtil(10000, () -> {});
//        lipSyncAnimator = new LipSyncAnimator(mouth);
    }

    /** 判断是否已连接 */
    public boolean isConnected() {
        return webSocket != null;
    }

    /** 建立WebSocket连接 */
    public void connect() {
        if (WS_URL.trim().isEmpty() || ACCESS_TOKEN.trim().isEmpty()
                || CLIENT_ID.trim().isEmpty() || BuildConfig.PATCHX_DEVICE_SN.trim().isEmpty()) {
            Log.w(TAG, "WebSocket is not configured; set patchx.* in local.properties.");
            return;
        }
        allowWakeUp = true;
        isReadyToSend.set(false);
        isDreamingOrFortune = false;
        allowPlay = true;
//        androidId = Settings.Secure.getString(
//                context.getContentResolver(),
//                Settings.Secure.ANDROID_ID
//        );

        client = new OkHttpClient.Builder()
                .connectTimeout(10, TimeUnit.SECONDS)
                .readTimeout(10, TimeUnit.SECONDS)
                .writeTimeout(10, TimeUnit.SECONDS)
                .build();

        Request request = new Request.Builder()
                .url(WS_URL)
                .addHeader("Authorization", "Bearer " + ACCESS_TOKEN)
                .addHeader("Device-Id", UnifiedRobotState.i().getSn())
                .addHeader("Client-Id", CLIENT_ID)
                .addHeader("Protocol-Version", "1")
                .build();

        webSocket = client.newWebSocket(request, new WebSocketListener() {
            @Override
            public void onOpen(WebSocket webSocket, Response response) {
                Log.i(TAG, "===测试==== WebSocket连接成功");
                WebSocketManager.this.webSocket = webSocket;
                EventBus.getDefault().post(new Events.WebSocketEventConnected());
                sendHelloMessage();
                timer_loop.start();
            }

            @Override
            public void onMessage(WebSocket webSocket, String text) {
                // Payload logging omitted from the public build.
                InactivityScreenOffWatcher.i().refresh();
                handleTextMessage(text);
            }

            @Override
            public void onMessage(WebSocket webSocket, ByteString bytes) {
                //Log.d(TAG, "===测试==== onMessage==收到音频流: " + bytes.size());
                if (allowPlay){
                    handleAudioMessage(bytes.toByteArray());
                }

            }


            @Override
            public void onClosing(WebSocket webSocket, int code, String reason) {
                Log.w(TAG, "===测试==== WebSocket关闭中: " + reason);
                EventBus.getDefault().post(new Events.WebSocketEventDisConnected());
                isAllowedToSend = true;
                isReadyToSend .set( false);
                disconnect();
            }

            @Override
            public void onClosed(WebSocket webSocket, int code, String reason) {
                Log.w(TAG, "===测试==== WebSocket已关闭: " + reason);
                isReadyToSend .set( false);
                allowWakeUp = true;
                DeviceManager.getInstance().setLcdBackLightEnable(false);
                firstWakeUp = true;
                disconnect();
            }

            @Override
            public void onFailure(WebSocket webSocket, Throwable t, Response response) {
                Log.e(TAG, "===测试==== WebSocket连接失败: " + t.getMessage());
                isReadyToSend .set( false);
                allowWakeUp = true;
                firstWakeUp = true;
                EmoteMessage msg = new EmoteMessage(1, "10");
                String json = msg.toJson();
                UnityManager.getInstance().send("PhotoManager", "OnPatchMessage", json);
                DeviceManager.getInstance().setLcdBackLightEnable(false);
                disconnect();
            }
        });
    }

    /** 断开连接 */
    public void disconnect() {
        isAllowedToSend = true;

        if (webSocket != null) {
            webSocket.close(1000, "正常关闭");
            webSocket = null;
        }
        if (client != null) {
            client.dispatcher().executorService().shutdown();
            client = null;
        }
    }
    /** 释放资源 */
    public void release_resource() {
        if (timer_loop != null) timer_loop.stop();
        if (opusEncoder != null) { opusEncoder.release(); opusEncoder = null; }
        if (opusDecoder != null) { opusDecoder.release(); opusDecoder = null; }
        audioPlayExecutor.shutdownNow();
        sendExecutor.shutdownNow();
        disconnect();
        context = null;
    }

    /** 发送 Hello 握手消息 */
    private void sendHelloMessage() {
        try {
            UUID uuidV7 = UuidCreator.getTimeOrdered();
            JSONObject helloMsg = new JSONObject();
            helloMsg.put("type", "hello");
            helloMsg.put("version", 1);
            helloMsg.put("transport", "websocket");
            helloMsg.put("client_ip",DeviceManager.getInstance().getIpInfo());
            helloMsg.put("device_id",UnifiedRobotState.i().getSn());
            helloMsg.put("trace_id", uuidV7.toString());
            JSONObject audioParams = new JSONObject();
            audioParams.put("format", "opus");
            audioParams.put("sample_rate", 16000);
            audioParams.put("channels", 1);
            audioParams.put("frame_duration", 60);

            helloMsg.put("audio_params", audioParams);

            if (webSocket != null) {
                webSocket.send(helloMsg.toString());
                EventBus.getDefault().post(new Events.GUI_LOG_Event_text( "Sending  Hello "));
            }
        } catch (JSONException e) {
            // callback.onLog("构建Hello消息失败: " + e.getMessage());
        }
    }


    /** 解析服务端文本消息 */
    private void handleTextMessage(String text) {
        try {
            JSONObject json = new JSONObject(text);
            if (!json.has("type")) {
                Log.d(TAG, "Message has no type field.");
                return ;
            }
            String type = json.getString("type");
            if ("hello".equals(type) && json.has("session_id")) {
                sessionId = json.getString("session_id");
                sendListenStartMessage();
                EventBus.getDefault().post(new Events.GUI_LOG_Event_text("会话已建立"));
            } else if ("tts".equals(type) && json.has("text") && "sentence_start".equals(json.getString("state"))) {
//                doUpdateUserInfo();
//                String sttText = json.getString("text");
//                if (json.has("emoji_map")) {
////                    String emojiMap = json.optString("emoji_map", "").trim();
////                    if (emojiMap.matches("\\d+")) { // 纯数字
////                        int value = Integer.parseInt(emojiMap);
////                        Log.d(TAG,"测试=====推送过来的文本===="+sttText+"===推送过来的表情和动作编码==="+emojiMap);
////                        DeviceManager.getInstance().sendDeviceCommand(value);
////                        emoteScheduler.sendEmote(emojiMap);
////                    } else {
//////                        int value = Math.random() < 0.5 ? 2 : 15;
//////                        DeviceManager.getInstance().sendDeviceCommand(value);
////                    }
//
//                } else {
//                    Log.w("DeviceManager", "json 中没有 emoji_map 字段");
//                }

//                EventBus.getDefault().post(new Events.WebSocketEvent_STT_text( sttText));
                //callback.onSttResult(sttText);
            } else if ("tts".equals(type) ) {
//                doUpdateUserInfo();
                if(json.has("state") && "start".equals(json.getString("state"))) {
                    isAllowedToSend = false;
                    boolean bl =false;
                    tts_is_running.set(true);
                    EventBus.getDefault().post(new Events.TTS_STATUS_EVENT(false));
                    //callback.onTTSTOP(bl);
                }else if( json.has("state") && "stop".equals(json.getString("state"))) {
                    isAllowedToSend = true;
                    //isReadyToSend = true;
                    boolean bl =true;
                    tts_is_running.set(false);
                    //callback.onTTSTOP(bl);
                    //callback.onLog("TTS播放开始");
                    EventBus.getDefault().post(new Events.TTS_STATUS_EVENT(true));
                    if (json.has("reason") && "nterrupt".equals(json.getString("reason"))){
                        AudioPlayer.getInstance().stopPlayback();
                    }
                }
                //callback.onLog("TTS播放结束");
            }else if("llm".equals(type)) {
                String emostr = json.getString("emotion");
                EventBus.getDefault().post(new Events.WebSocketEvent_EMO_text(emostr));
                // 若收到pong响应，通知心跳管理器
            }else if ( "pong".equals(type)) {
                //heartBeatManager.onPongReceived();
            }else if("mcp".equals(type)){
                Log.e(TAG,"mcp recv");
                // Do not log the service payload.
                JSONObject payload = json.getJSONObject("payload");

                String method = payload.getString("method");
                JSONObject params = payload.getJSONObject("params");

                //Log.d(TAG, "解析到query: " + query);
                // 原有逻辑：如果method是vision_recognition则执行操作
                if ("vision_recognition".equals(method)) {
                    //byte[] jpgbuf = getAssetsImageBuffer(context,"man.jpg");
                    //String imageBase64 = encodeAssetsImageToBase64(context,"man.jpg");

                    if(true) {
                        byte[] jpgbuffer = ImageProcessing.getInstance().takePicture("");
                        String querytext = params.getString("query");
                        sendJpgData(jpgbuffer, querytext);
                        //MainActivity.imgsaver.saveJpgToPrivateDir(jpgbuffer,"iuiu");
                    }else
                    {
                        String obj_info = ImageProcessing.getInstance().ImageRecog();
                        String prompt_str = "请根据分类模型给出的识别结果，分析识别到了哪些物体。\n\n 模型输出：" + obj_info;
                        sendTextMsg(prompt_str);
                        Log.d(TAG,prompt_str);
                    }

                }else if("secondary_vision_capture".equals(method)){
                    byte [] jpgbuffer = ImageProcessing.getInstance().takePicture("");
                    String querytext = params.getString("query");
                    sendJpgData(jpgbuffer,querytext);
                    //MainActivity.imgsaver.saveJpgToPrivateDir(jpgbuffer,"iuiu");

                }if ("set_volume".equals(method)) {
                    int value = params.optInt("volume", -1);
                    DeviceManager.getInstance().setVolume(value);

                } else if ("adjust_volume".equals(method)) {
                    int delta = params.optInt("adjustment", 0);
                    DeviceManager.getInstance().setVolume(delta);

                } else if ("set_brightness".equals(method)) {
                    int value = params.optInt("brightness", -1);
                    DeviceManager.getInstance().setBrightness(value);
                } else if ("adjust_brightness".equals(method)) {
                    int delta = params.optInt("adjustment", 0);
                    DeviceManager.getInstance().setBrightness(delta);
                } else if ("action_control".equals(method)) {
                    JSONObject actionParams = payload.getJSONObject("params");
                    if (actionParams.has("action_type")) {
                        int action = actionParams.optInt("action_type", 0);
                        if (action != 0){
                            DeviceManager.getInstance().sendDeviceCommand(action);
                            Log.d(TAG, "测试=====发送动作系统编号===: " + action);
                        }
                    } else {
                        Log.w("action_control", "action_control");
                    }
                }else if ("expression_control".equals(method)) {
                    JSONObject actionParams = payload.getJSONObject("params");
                    if (actionParams.has("expression_type")) {
                        int expression = actionParams.optInt("expression_type", 0);
                        if (expression != 0){
                            EmoteMessage msg = new EmoteMessage(1, String.valueOf(expression));
                            String emoteJson = msg.toJson();
                            UnityManager.getInstance().send("PhotoManager", "OnPatchMessage", emoteJson);
                            Log.d(TAG, "测试=====发送表情系统===Emote -> Unity: " + emoteJson);
                        }
                    } else {
                        Log.w("expression_control", "expression_control");
                    }
                }

            } else if ("goodbye".equals(type)) {
                allowWakeUp = false;
//                DeviceManager.getInstance().setLcdBackLight(0);
                EmoteMessage msg = new EmoteMessage(1, "10");
                String emoteJson = msg.toJson();
                UnityManager.getInstance().send("PhotoManager", "OnPatchMessage", emoteJson);
                DeviceManager.getInstance().sendDeviceCommand(10);
            }else if("welcome".equals(type)){
                EmoteMessage msg = new EmoteMessage(1, "5");
                String emoteJson = msg.toJson();
                UnityManager.getInstance().send("PhotoManager", "OnPatchMessage", emoteJson);
                DeviceManager.getInstance().sendDeviceCommand(5);
            }else if("emoji".equals(type)){
                doUpdateUserInfo();
                if (json.has("emoji_map")) {
                    String emojiMap = json.optString("emoji_map", "").trim();
                    if (emojiMap.matches("\\d+")) { // 纯数字
                        int value = Integer.parseInt(emojiMap);
                        DeviceManager.getInstance().sendDeviceCommand(value);
                        emoteScheduler.sendEmote(emojiMap);
                    }else{
                        int[] arr = {12, 13, 14,15, 21, 22, 23, 24};
                        int value = arr[(int)(Math.random() * arr.length)];
                        DeviceManager.getInstance().sendDeviceCommand(value);
                    }
                }
            }else if("interrupt_complete".equals(type)){
                allowPlay = true;
            }else{
                Log.d(TAG,type);
                Logger.e(TAG,type);
            }

            String ttsType = json.getString("tts_type");
            if ("music".equals(ttsType)){
                if (json.has("state") && "sentence_start".equals(json.getString("state"))){
                    isReadyToSend.set(false);
//                    audioLevelAnalyzer.allowSend = false;
                    DeviceManager.getInstance().sendDeviceCommand(26);
                } else if (json.has("state") && "sentence_end".equals(json.getString("state"))) {
                    new Handler(Looper.getMainLooper()).postDelayed(() -> {
                        isReadyToSend.set(true);
                        EmoteMessage msg = new EmoteMessage(1, "4");
                        String emoteJson = msg.toJson();
                        UnityManager.getInstance().send("PhotoManager", "OnPatchMessage", emoteJson);
//                        audioLevelAnalyzer.allowSend = true;
                    }, 1000);
                }else{
                    Log.d(TAG,ttsType);
                    Logger.e(TAG,ttsType);
                }

            }

        } catch (JSONException e) {
            //callback.onLog("解析消息失败: " + e.getMessage());
            Logger.e(TAG,"解析消息失败: " + e.getMessage());
        }
    }

    public void doUpdateUserInfo(){
        try{
            String faceinfo = ImageProcessing.getInstance().GetFaceInfo();
            JSONObject obj = new JSONObject(faceinfo);
            int faceId = obj.getInt("face_id");
            if (faceId > 0){
                sendUpdateUserId(faceId);
            }
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    /** 处理TTS状态 */
    private void handleTtsState(JSONObject json) throws JSONException {
        if ("start".equals(json.optString("state"))) {
            isAllowedToSend = false;
            tts_is_running.set(true);
            EventBus.getDefault().post(new Events.TTS_STATUS_EVENT(false));
        } else if ("stop".equals(json.optString("state"))) {
            isAllowedToSend = true;
            tts_is_running.set(false);
            EventBus.getDefault().post(new Events.TTS_STATUS_EVENT(true));
        }
    }

    /** 处理服务端音频数据 */
    // 收到一帧 Opus 压缩数据：先解码成 16k/mono/16bit PCM
    private void handleAudioMessage(byte[] audioData) {
        audioPlayExecutor.execute(() -> {
            try {
                // Opus 解码器必须配置输出 16k 单声道
                short[] pcm = opusDecoder.decode(audioData,frameSize);
                if (pcm == null || pcm.length == 0) return;
                // 播放整帧
                AudioPlayer.getInstance().playAudio(pcm);
                audioLevelAnalyzer.feed(pcm);
                // 嘴形分析
//                float[] vis = new float[15];
//                int ret = OVRLipSyncJNI.process(pcm, vis);
//                if (ret != 0) {
//                    Log.e("===测试==== 输出嘴形数据===", "OVRLipSync process failed: " + Arrays.toString(vis));
//                } else {
//                    Log.e("===测试==== 输出嘴形数据===", "OVRLipSync process success===: " + Arrays.toString(vis));
//                }
//                lipSyncAnimator.updateMouth(vis);
            } catch (Throwable t) {
                Log.e("LipSync", "audio err", t);
            }
        });
    }
    private static short[] copy320(short[] src, int off) {
        short[] dst = new short[320];
        System.arraycopy(src, off, dst, 0, 320);
        return dst;
    }
    private long calculatePlaybackTimestamp(int frameLength) {
        AudioTrack audioTrack = AudioPlayer.getInstance().getAudioTrack();
        if (audioTrack == null) return System.currentTimeMillis();
        int bufferDelayMs = 60;
        return System.currentTimeMillis() + bufferDelayMs;
    }


    public void sendTextMsg(String text){
        /*

                                        "session_id": session_id,
                                "type": "listen",
                                "state": "detect",
                                "mode": "manual",
                                "text":"你看我手里有什么"
         */

        try{
            JSONObject startMsg = new JSONObject();
            startMsg.put("type", "listen");
            startMsg.put("state", "detect");
            startMsg.put("mode", "manual");
            startMsg.put("text", text);

            if (webSocket != null) {
                webSocket.send(startMsg.toString());
            }

        } catch (JSONException e) {
            Log.e(TAG, "===测试==== 构建start消息失败: " + e.getMessage());
        }
    }


    public void sendUpdateUserId(int userId){
        try{
            JSONObject startMsg = new JSONObject();
            startMsg.put("type", "updateUserId");
            startMsg.put("state", "update");
            startMsg.put("user_id", String.valueOf(userId));
            if (webSocket != null) {
                webSocket.send(startMsg.toString());
            }

        } catch (JSONException e) {
            Log.e(TAG, "===测试==== sendUpdateUserId失败: " + e.getMessage());
        }
    }

    public void sendJpgData(byte[] jpegbuf,String querytext){

        String imageBase64 = Base64.getEncoder().encodeToString(jpegbuf);

        if (imageBase64 == null) {
            return ;
        }
        try{
            JSONObject startMsg = new JSONObject();
            startMsg.put("type", "image");
            startMsg.put("text", querytext);
            // 3. 构建image_data子对象
            JSONObject imageData = new JSONObject();
            imageData.put("data", imageBase64);  // 图片Base64数据
            imageData.put("format", "jpeg");     // 图片格式
            imageData.put("url", "");            // 空URL

            // 4. 将image_data放入外层对象
            startMsg.put("image_data", imageData);

            if (webSocket != null) {
                webSocket.send(startMsg.toString());
            }
            //Log.e(TAG,"Send jpg");
        } catch (JSONException e) {
            Log.e(TAG, "===测试==== 构建start消息失败: " + e.getMessage());
        }
    }

    /** 发送单帧 PCM 数据（会做补零到960short） */
    public void sendPcmAudioData(short[] pcmData) {
        if (pcmData == null || pcmData.length != 960) {
            Log.w(TAG, "===测试==== PCM 数据不合法，必须是960长度");
            return;
        }
        if (opusEncoder == null || webSocket == null) {
            Log.w(TAG, "===测试==== 编码器或WebSocket未初始化，丢弃帧");
            return;
        }
        try {
            byte[] opusData = opusEncoder.encode(pcmData, pcmData.length);
            if (opusData == null) {
                Log.w(TAG, "===测试==== Opus 编码返回空，丢弃该帧");
                return;
            }
            webSocket.send(ByteString.of(opusData));
            //Log.d(TAG, "===测试==== 发送音频帧 short=" + pcmData.length + " byte=" + opusData.length);
        } catch (Exception e) {
            Log.e(TAG, "===测试==== Opus编码异常: " + e.getMessage());
        }
    }


    public byte[]  pcm2opus(short[] pcmData) {
            byte[] opusData = opusEncoder.encode(pcmData, pcmData.length);
            return opusData;
    }

    /** 向服务端发送listen开始 */
    public void sendListenStartMessage() {

        if (!isDreamingOrFortune){
            try {
                JSONObject startMsg = new JSONObject();
                startMsg.put("session_id", sessionId != null ? sessionId : CLIENT_ID);
                startMsg.put("type", "listen");
                startMsg.put("state", "start");
                startMsg.put("mode", LISTEN_MODE);


                if (webSocket != null) {
                    webSocket.send(startMsg.toString());
                    // 延迟 3 秒设置 isReadyToSend = true
                    new Handler(Looper.getMainLooper()).postDelayed(() -> {
                        isReadyToSend .set( true);
                        Log.d(TAG, "===测试==== 0.1秒后允许发送音频");
                    }, 100);
                }
            } catch (JSONException e) {
                Log.e(TAG, "===测试==== 构建start消息失败: " + e.getMessage());
            }
        }

    }

    /** 向服务端发送listen结束 */
    public void sendListenStopMessage() {

        if (!LISTEN_MODE.contains("manual"))
            return;

        if (!isDreamingOrFortune){
            if (LISTEN_MODE.contains("auto")) return;
            try {
                JSONObject stopMsg = new JSONObject();
                stopMsg.put("session_id", sessionId != null ? sessionId : CLIENT_ID);
                stopMsg.put("type", "listen");
                stopMsg.put("state", "stop");


                if (webSocket != null) webSocket.send(stopMsg.toString());
            } catch (JSONException ignored) {}
        }
    }

    /**打断**/
    public void sendAbortMessage() {
        try {
            JSONObject abortMsg = new JSONObject();
            abortMsg.put("type", "abort");
            abortMsg.put("session_id", sessionId != null ? sessionId : CLIENT_ID);

            if (webSocket != null) {
                webSocket.send(abortMsg.toString());
//                new Handler(Looper.getMainLooper()).postDelayed(() -> {
//                    audioLevelAnalyzer.allowSend = true;
//                }, 500);

            }
        } catch (JSONException e) {
            //callback.onLog("构建Abort消息失败: " + e.getMessage());
        }
    }


    public void sendInterruptMessage(){
        try {
            allowPlay = false;
            JSONObject abortMsg = new JSONObject();
            abortMsg.put("type", "interrupt");
            abortMsg.put("session_id", sessionId != null ? sessionId : CLIENT_ID);

            if (webSocket != null) {
                webSocket.send(abortMsg.toString());
            }
        } catch (JSONException e) {
            //callback.onLog("构建Abort消息失败: " + e.getMessage());
        }
    }

    public void stopPlayAudio(){
        AudioPlayer.getInstance().stopPlayback();
    }




    /**
     * 将图片文件编码为Base64字符串
     * @param imagePath 图片路径
     * @return Base64字符串（失败返回null）
     */
    private static String encodeImageToBase64(String imagePath) {
        File file = new File(imagePath);
        if (!file.exists()) {
            return null;
        }

        try (FileInputStream fis = new FileInputStream(file)) {
            // 读取文件字节
            byte[] buffer = new byte[(int) file.length()];
            int bytesRead = fis.read(buffer);
            if (bytesRead == -1) {
                return null;
            }

            // 编码为Base64（注意使用NO_WRAP避免换行符）
            return Base64.getEncoder().encodeToString(buffer);

        } catch (IOException e) {
            e.printStackTrace();
            return null;
        }
    }
    public static String encodeAssetsImageToBase64(Context context, String assetsPath) {
        InputStream is = null;
        ByteArrayOutputStream baos = null;
        try {
            // 1. 通过AssetManager打开assets中的文件
            is = context.getAssets().open(assetsPath);

            // 2. 读取文件字节到ByteArrayOutputStream
            baos = new ByteArrayOutputStream();
            byte[] buffer = new byte[1024];
            int len;
            while ((len = is.read(buffer)) != -1) {
                baos.write(buffer, 0, len);
            }
            baos.flush();

            // 3. 转换为Base64（NO_WRAP避免换行符）
            byte[] imageBytes = baos.toByteArray();
            return Base64.getEncoder().encodeToString(imageBytes);

        } catch (IOException e) {
            e.printStackTrace();
            return null;
        } finally {
            // 4. 关闭流
            try {
                if (is != null) is.close();
                if (baos != null) baos.close();
            } catch (IOException e) {
                e.printStackTrace();
            }
        }
    }


    public static  byte[] getAssetsImageBuffer(Context context, String assetsPath) {
        InputStream is = null;
        ByteArrayOutputStream baos = null;
        try {
            // 1. 通过AssetManager打开assets中的文件
            is = context.getAssets().open(assetsPath);
            // 2. 读取文件字节到ByteArrayOutputStream
            baos = new ByteArrayOutputStream();
            byte[] buffer = new byte[1024];
            int len;
            while ((len = is.read(buffer)) != -1) {
                baos.write(buffer, 0, len);
            }
            baos.flush();

            return baos.toByteArray();

        } catch (IOException e) {
            e.printStackTrace();
            return null;
        } finally {
            // 4. 关闭流
            try {
                if (is != null) is.close();
                //if (baos != null) baos.close();
            } catch (IOException e) {
                e.printStackTrace();
            }
        }
    }

    public void sendSocketMessage(SocketEvent event){
        try {
            if (event.action.equals("start")){
                isDreamingOrFortune = true;
            }else{
                isDreamingOrFortune = false;
            }
            JSONObject infoMsg = new JSONObject();
            infoMsg.put("type", "listen_mode");
            infoMsg.put("action",event.action);
            infoMsg.put("session_type",event.sessionType);
            if (webSocket != null) {
                // Payload logging omitted from the public build.
                webSocket.send(infoMsg.toString());
            }
        } catch (JSONException e) {
        }
    }
}
