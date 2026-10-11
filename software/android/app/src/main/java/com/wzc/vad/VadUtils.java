package com.wzc.vad;

import android.util.Log;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.List;

public class VadUtils implements AutoCloseable {
    private static final String TAG = "VadUtils";

    static {
        try {
            System.loadLibrary("ten_vad_jni");
            Log.d(TAG, "libten_vad_jni loaded");
        } catch (Throwable t) {
            Log.e(TAG, "Failed to load libten_vad_jni: " + t.getMessage(), t);
        }
    }

    // native 接口
    private native void   create();
    private native void   free();
    private native int    init(float threshold);
    public  native int    process(int fs, short[] buffer, int length);
    public  native boolean    validRateAndFrameLength(int fs, int length);

    public static final int SAMPLE_RATE = 16000;
    public static final int HOP_SIZE    = 256;

    private boolean inited = false;
    private float   threshold = 0.1f;
    private final short[] window = new short[HOP_SIZE];
    private int     filled = 0;

    public VadUtils() {}
    public VadUtils(float threshold) { setThreshold(threshold); }

    public void setThreshold(float threshold) {
        if (Float.isNaN(threshold)) threshold = 0.15f;
        if (threshold < 0f) threshold = 0f;
        if (threshold > 1f) threshold = 1f;
        this.threshold = threshold;
    }

    /** 运行时调灵敏度（阈值越大越严格）：立即重建一次 */
    public synchronized boolean setThresholdAndReinit(float newThreshold) {
        setThreshold(newThreshold);
        return init();
    }

    /** 初始化 JNI（可重复调用；会先 free 再 create/init） */
    public synchronized boolean init() {
        try {
            if (inited) {
                try { free(); } catch (Throwable ignored) {}
                inited = false;
            }
            create();
            int r = init(threshold);
            inited = (r == 0);
            if (!inited) {
                Log.e(TAG, "VAD init failed, ret=" + r + " (threshold=" + threshold + ")");
            } else {
                Log.d(TAG, "VAD init ok, threshold=" + threshold);
            }
        } catch (Throwable t) {
            Log.e(TAG, "init exception: " + t.getMessage(), t);
            inited = false;
        }
        filled = 0;
        return inited;
    }

    public boolean isInited() { return inited; }

    @Override
    public synchronized void close() {
        try { free(); } catch (Throwable ignored) {}
        inited = false;
        filled = 0;
    }

    /** 必须传 256 样本 */
    public int processFrame256(short[] frame256) {
        if (!inited) { Log.e(TAG, "processFrame256: not inited"); return -1; }
        if (frame256 == null || frame256.length != HOP_SIZE) {
            Log.e(TAG, "processFrame256: invalid frame length=" + (frame256 == null ? 0 : frame256.length));
            return -1;
        }
        try {
            return process(SAMPLE_RATE, frame256, HOP_SIZE);
        } catch (Throwable t) {
            Log.e(TAG, "processFrame256 exception: " + t.getMessage(), t);
            return -1;
        }
    }

    /** 按 256 自动分帧，返回每帧判定（1/0/-1）。不会分配多余对象。 */
    public List<Integer> push(short[] data, int offset, int length) {
        List<Integer> out = new ArrayList<>();
        if (!inited) { Log.e(TAG, "push: not inited"); return out; }
        if (data == null || length <= 0 || offset < 0 || offset + length > data.length) {
            Log.e(TAG, "push: invalid args offset=" + offset + " length=" + length);
            return out;
        }

        int pos = offset, end = offset + length;
        while (pos < end) {
            int need = HOP_SIZE - filled;
            int copy = Math.min(need, end - pos);
            System.arraycopy(data, pos, window, filled, copy);
            filled += copy;
            pos    += copy;

            if (filled == HOP_SIZE) {
                int flag;
                try { flag = process(SAMPLE_RATE, window, HOP_SIZE); }
                catch (Throwable t) { Log.e(TAG, "push/process exception: " + t.getMessage(), t); flag = -1; }
                out.add(flag);
                filled = 0;
            }
        }
        return out;
    }

    public List<Integer> push(short[] data) { return push(data, 0, (data==null?0:data.length)); }

    /** 便捷：byte[](LE)→short[] 拼帧再判定（避免上层自己转） */
    public List<Integer> push(byte[] lePcm16, int offset, int length) {
        if (lePcm16 == null || length <= 1 || offset < 0 || offset + length > lePcm16.length) {
            return new ArrayList<>();
        }
        // 把 byte[] 视作 LE 16-bit
        int samples = length / 2;
        short[] tmp = new short[samples];
        ByteBuffer.wrap(lePcm16, offset, samples*2).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(tmp);
        return push(tmp, 0, tmp.length);
    }

    public int isValidCombo(int fs, int length) {
        try {
            return validRateAndFrameLength(fs, length) ? 1 : 0;
        } catch (Throwable t) {
            Log.w(TAG, "validRateAndFrameLength exception: " + t.getMessage());
            return (fs == SAMPLE_RATE && (length % HOP_SIZE) == 0) ? 1 : 0;
        }
    }
}
