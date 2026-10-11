package com.patchx_main.patchx.manage;


import com.patchx_main.patchx.voice.AudioUtils;

import com.patchx_main.patchx.voice.PCMVoiceProcess2;

public class WakeAudioStreamManager {
    private static final String TAG = "WakeAudioStreamManager";

    private final WebSocketManager webSocketManager;

//    PCMVoiceProcess pcmVoiceProcessor;
//    public WakeAudioStreamManager(WebSocketManager wsManager) {
//        this.webSocketManager = wsManager;
//
//        pcmVoiceProcessor = new PCMVoiceProcess(wsManager);
//    }
//    public void release(){
//        pcmVoiceProcessor.close();
//        pcmVoiceProcessor = null;
//    }
//    public void onAudioFrame(byte[] bytes, int len) {
//        // 1. 字节数组转16bit PCM（样本数 = 字节数/2）
//        short[] pcmData = AudioUtils.bytesToShorts(bytes);
//        // 2. 将数据交给音频处理器处理，立即返回
//        if (pcmVoiceProcessor != null) {
//            try {
//                pcmVoiceProcessor.addAudioData(pcmData);
//            } catch (Exception e) {
//                throw new RuntimeException(e);
//            }
//
//        }
//
//    }


    PCMVoiceProcess2 pcmVoiceProcessor2;
    public WakeAudioStreamManager(WebSocketManager wsManager) {
        this.webSocketManager = wsManager;

        pcmVoiceProcessor2= new PCMVoiceProcess2(wsManager);
    }
    public void release(){
        pcmVoiceProcessor2 = null;
    }
    public void onAudioFrame(byte[] bytes, int len) {
        // 1. 字节数组转16bit PCM（样本数 = 字节数/2）
        short[] pcmData = AudioUtils.bytesToShorts(bytes);
        // 2. 将数据交给音频处理器处理，立即返回
        if (pcmVoiceProcessor2 != null) {
            try {
                pcmVoiceProcessor2.onAudioFrame(pcmData);
            } catch (Exception e) {
                throw new RuntimeException(e);
            }

        }

    }
}
