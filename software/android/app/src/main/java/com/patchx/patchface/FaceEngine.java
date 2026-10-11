package com.patchx.patchface;

import android.content.res.AssetManager;
import android.util.Log;

public class FaceEngine {
    private static final String TAG = "FaceEngine";
    static {
        try {
            System.loadLibrary("patchx-face");
            Log.i(TAG, "patchx face loaded OK");
        } catch (UnsatisfiedLinkError e) {
            Log.e(TAG, "load so failed", e);
        }
    }

    // ===== Native bindings =====
    private native boolean  faceInit(AssetManager assetManager, String retinaPath, String mobilePath);
    private native FaceInfo faceDetect(byte[] yuvData, int width, int height);

    // 新增：从 NV21 + 人脸框中提取特征（返回 L2 归一化后的 float[]）
    private native float[]  faceGetFeature(byte[] yuvData,
                                           int width, int height,
                                           int x, int y, int boxW, int boxH);
    private native float[]  faceGetFeatureInfo(byte[] yuvData,
                                           int width, int height,
                                           int x, int y, int boxW, int boxH);

    private native void     faceRelease();

    private boolean initialized = false;

    public boolean init(AssetManager assetManager, String retinaModelPath, String mobileModelPath) {
        initialized = faceInit(assetManager, retinaModelPath, mobileModelPath);
        Log.i(TAG, "nativeInit result = " + initialized);
        return initialized;
    }

    public FaceInfo detectFace(byte[] yuvData, int width, int height) {
        if (!initialized) {
            Log.w(TAG, "FaceEngine not initialized!");
            return null;
        }
        return faceDetect(yuvData, width, height);
    }

    // 便捷包装：直接用 FaceInfo 调用
    public float[] getFeature(byte[] yuvData, int width, int height, FaceInfo fi) {
        if (!initialized) {
            Log.w(TAG, "FaceEngine not initialized!");
            return null;
        }
        if (fi == null) return null;
        return faceGetFeatureInfo(yuvData, width, height, fi.x, fi.y, fi.w, fi.h);
    }

    // 也支持传自定义框
    public float[] getFeature(byte[] yuvData, int width, int height, int x, int y, int w, int h) {
        if (!initialized) {
            Log.w(TAG, "FaceEngine not initialized!");
            return null;
        }
        return faceGetFeature(yuvData, width, height, x, y, w, h);
    }

    public void release() {
        if (initialized) {
            faceRelease();
            initialized = false;
            Log.i(TAG, "FaceEngine released");
        }
    }
}
