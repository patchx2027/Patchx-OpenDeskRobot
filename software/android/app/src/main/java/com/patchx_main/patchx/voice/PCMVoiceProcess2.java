package com.patchx_main.patchx.voice;

import static java.util.Arrays.fill;

import android.util.Log;

import com.patchx_main.patchx.manage.WebSocketManager;
import com.wzc.vad.VadUtils;

import java.util.concurrent.atomic.AtomicBoolean;

public class PCMVoiceProcess2{
    private static final String TAG = "PCMVoiceProcess";
    private  final  WebSocketManager ws;


    private final int VAD_FRAME_SIZE = 960;
    private int silenceFrames = MIN_SILENCE_FRAMES+100;
    private final short[] cache = new short[VAD_FRAME_SIZE * 10];
    private int cachePos = 0;
    private  int voiceFrames = 0;


    private static final int   MIN_SILENCE_FRAMES = 5; // 连续静音帧数阈值（以 256 为一帧）
    private static final float INITIAL_BG_RMS = 800f;  // 初始底噪（经验值 300~800）
    private static  final float filter_alpha = 0.99f;
    private static final float SPEECH_BOOST   = 1.5f;  // 相对门限倍数（1.5~2.0 越大越严）
    private static final float VAD_THRESHOLD  = 0.01f; // VAD 内部概率阈值（0~1，越大越严）
    float bgRms = INITIAL_BG_RMS;  // 自适应底噪初值

    //private short [] base_voice=new short[16000*3];
    private short [] curr_voice=new short[16000*3];
    int curr_voice_len = 0;

    private final VadUtils vad = new VadUtils(VAD_THRESHOLD);
    private AtomicBoolean voice_actived=new AtomicBoolean(false);
    public PCMVoiceProcess2(WebSocketManager ws){
        this.ws = ws;
        //vad.setThreshold(VAD_THRESHOLD);
        vad.init();
        curr_voice_len = 0;
    }
    public void release(){
        vad.close();
    }

    public void onAudioFrame(short[] pcmData){
        System.arraycopy(pcmData, 0, cache, cachePos, pcmData.length);
        cachePos += pcmData.length;
        while (cachePos >=960) {
            short[] frame = new short[960];
            System.arraycopy(cache, 0, frame, 0, 960);
            ws.sendPcmAudioData(frame);
            // 移动剩余
            cachePos -= 960;
            if (cachePos > 0) {
                System.arraycopy(cache, 960, cache, 0, cachePos);
            }
        }
        doAudioVad(pcmData);
    }

    public void doAudioVad(short[] pcmData){
        short[] gainedData = AudioAmplifier.amplifyToTargetMean(pcmData,30);
        // 计算当前  样本能量
        float micRms = rms(gainedData, 0, pcmData.length);
        // 相对门限：能量不足→直接判静音，节省 JNI，且远处小声不会触发
        int vadFlag=1;
        if (micRms < bgRms * SPEECH_BOOST) {
            vadFlag = 0;
        } else {
            if (!run_VAD(16000,gainedData)){
                vadFlag = 0;            // 出错按静音处理
            }
        }
        if( vadFlag==0){
            silenceFrames++;
            if (silenceFrames >= MIN_SILENCE_FRAMES) {
                voiceFrames = 0;      // ★ 为下一次触发做准备（关键修复点）
//                Log.d(TAG, "测试=====静音啦===");
            }
        }else {
            voiceFrames ++;
            if( voiceFrames==5) {
                Log.d(TAG, "测试=====被打断啦===");
                silenceFrames=0;
                ws.stopPlayAudio();
                ws.sendInterruptMessage();
            }else{
                //Log.d(TAG,"IIIIIIIIIIIIIIIIIII");
            }
            silenceFrames=0;
        }

        if(silenceFrames< MIN_SILENCE_FRAMES) {
            if (curr_voice_len < curr_voice.length - pcmData.length) {
                System.arraycopy(pcmData, 0, curr_voice, curr_voice_len, pcmData.length);
                curr_voice_len += pcmData.length;
            }
        }
    }

    boolean run_VAD(int fs,short [] pcmData){
        // 定义前半段和后半段的长度（均为256）
        int segmentLength = 256;
        // 1. 提取前半段（索引0-255）
        short[] firstSegment = new short[segmentLength];
        System.arraycopy(pcmData, 0, firstSegment, 0, segmentLength);
        // 处理前半段
        int retFirst =vad.processFrame256(firstSegment);// vad.process(fs, firstSegment, segmentLength);
        // 2. 提取后半段（索引256-511）
        short[] secondSegment = new short[segmentLength];
        System.arraycopy(pcmData, segmentLength, secondSegment, 0, segmentLength);
        // 处理后半段
        int retSecond =vad.processFrame256(secondSegment);// vad.process(fs, secondSegment, segmentLength);
        //Log.d(TAG,"vad1:"+retFirst +",vad2:"+retSecond);
        return  retFirst == 1 &&  retSecond==1;
    }

    private static float rms(short[] buf, int off, int len) {
        long sum = 0;
        for (int i = 0; i < len; i++) {
            int s = buf[off + i];
            sum += (long) s * s;
        }
        return (float) Math.sqrt((double) sum / Math.max(1, len));
    }


}
