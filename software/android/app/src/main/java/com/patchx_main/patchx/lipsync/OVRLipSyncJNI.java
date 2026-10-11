package com.patchx_main.patchx.lipsync;


public final class OVRLipSyncJNI {
    static {
        System.loadLibrary("ovrlip_bridge");
    }
    public static native int  init(int sampleRate, int frameSize);
    public static native int  process(short[] pcmMono, float[] out15);
    public static native void release();
}
