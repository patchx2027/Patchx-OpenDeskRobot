package com.patchx_main.patchx.voice;

// Events.java
public class Events {

    public static class BTN_CONNECT_EVT{
        boolean is_connect_msg;
        public BTN_CONNECT_EVT(boolean bl){
            is_connect_msg=bl;
        }
    }

    // 新增：播放状态事件（true=开始播放，false=播放结束）
    public static class AUDIO_PLAY_STATE_Event {
        public boolean isPlaying;
        public AUDIO_PLAY_STATE_Event(boolean isPlaying) {
            this.isPlaying = isPlaying;
        }
    }

    public static class AUDIO_PCM_Event{
        public short[] pcm_data;
        public  AUDIO_PCM_Event(short[] pcm_data){
            this.pcm_data = pcm_data;
        }
    }


    public static class GUI_LOG_Event_text {
        String text;
        public GUI_LOG_Event_text(String txt){
            text = txt;
        }
    }

    public static class WebSocketEventConnected {
    }

    public static class WebSocketEventDisConnected {
    }

    public static class WebSocketEvent_STT_text {
        String text;
        public WebSocketEvent_STT_text(String txt){
            text=txt;
        }
    }


    public static class WebSocketEvent_EMO_text {
        String text;
        public WebSocketEvent_EMO_text(String txt){
            text=txt;
        }
    }


    public static  class StopAudioServiceEvent {
        // 可以添加额外参数（如停止原因）
    }

    public static class TTS_STATUS_EVENT{
        boolean is_stop;
       public TTS_STATUS_EVENT(boolean is_stop){
            this.is_stop = is_stop;
        }
    }

    public static class VAD_STATUS_EVENT{
        boolean is_stop;
        public VAD_STATUS_EVENT(boolean is_stop){
            this.is_stop = is_stop;
        }
    }
    public  static class LLM_ABORT_EVENT{

    }
    // 其他事件类...
}
