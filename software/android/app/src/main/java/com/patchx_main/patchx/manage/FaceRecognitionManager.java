package com.patchx_main.patchx.manage;

import android.annotation.SuppressLint;
import android.content.Context;
import android.util.Log;
import android.util.Size;

import androidx.annotation.NonNull;
import androidx.camera.core.CameraSelector;
import androidx.camera.core.ImageAnalysis;
import androidx.camera.core.ImageProxy;
import androidx.camera.core.resolutionselector.AspectRatioStrategy;
import androidx.camera.core.resolutionselector.ResolutionSelector;
import androidx.camera.lifecycle.ProcessCameraProvider;
import androidx.core.content.ContextCompat;
import androidx.lifecycle.LifecycleOwner;

import com.google.common.util.concurrent.ListenableFuture;
import com.patchx.patchface.FaceCompare;
import com.patchx.patchface.FaceDb;
import com.patchx.patchface.FaceEngine;
import com.patchx.patchface.FaceInfo;

import java.nio.ByteBuffer;
import java.util.Arrays;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;


public class FaceRecognitionManager {

    public interface OnRecognitionListener {
        /** 每次检测出人脸的位姿/方向（可选） */
        default void onFaceDirection(String dir, float offsetXNorm) {}
        /** 每次拿到特征向量（可选） */
        default void onFeature(float[] feat) {}
        /** 识别成功（FaceCompare内部也会处理，这里只是回调通知） */
        default void onRecognizedLog(String msg) {}
        /** 图像或推理异常 */
        default void onError(Throwable t) {}
        /** 没有检测到人脸（节流后才会回调） */
        default void onNoFace() {}
    }

    private static final String TAG = "FRManager";

    private final Context appCtx;
    private final LifecycleOwner lifecycleOwner;
    private final FaceEngine faceEngine;
    private final FaceDb faceDb;

    /** 是否对 NV21 进行 180° 旋转（你的硬件镜头装反时设为 true） */
    private final boolean rotate180;

    /** 处理帧最小间隔（毫秒）——避免过于频繁的推理 */
    private long PROCESS_GAP_MS = 1000;

    /** 启动时跳过前 N 帧，等 AE/AF/AWB 稳定 */
    private int skipFrames = 5;

    /** 单线程推理，避免堆积 */
    private final ExecutorService inferExec = Executors.newSingleThreadExecutor();
    private final AtomicBoolean busy = new AtomicBoolean(false);
    private volatile boolean started = false;

    private OnRecognitionListener listener;

    // 相机
    private ProcessCameraProvider cameraProvider;
    private ImageAnalysis imageAnalysis;

    // 时间戳节流
    private long lastProcessTs = 0L;

    public FaceRecognitionManager(
            @NonNull Context context,
            @NonNull LifecycleOwner lifecycleOwner,
            @NonNull FaceEngine faceEngine,
            @NonNull FaceDb faceDb,
            boolean rotate180
    ) {
        this.appCtx = context.getApplicationContext();
        this.lifecycleOwner = lifecycleOwner;
        this.faceEngine = faceEngine;
        this.faceDb = faceDb;
        this.rotate180 = rotate180;
    }

    public void setListener(OnRecognitionListener l) {
        this.listener = l;
    }

    /** 调整最小处理间隔（毫秒） */
    public void setProcessGapMs(long ms) { this.PROCESS_GAP_MS = Math.max(0, ms); }

    /** 启动（需已授予 CAMERA 权限；需在主线程调用） */
    public void start() {
        if (started) return;
        started = true;

        final ListenableFuture<ProcessCameraProvider> providerFuture =
                ProcessCameraProvider.getInstance(appCtx);

        providerFuture.addListener(() -> {
            try {
                cameraProvider = providerFuture.get();
                bindAnalysis();
            } catch (Throwable t) {
                started = false;
                if (listener != null) listener.onError(t);
                Log.e(TAG, "CameraProvider init failed", t);
            }
        }, ContextCompat.getMainExecutor(appCtx));
    }

    /** 停止（需在主线程调用） */
    public void stop() {
        started = false;
        try {
            if (cameraProvider != null) {
                cameraProvider.unbindAll();
            }
        } catch (Throwable t) {
            Log.w(TAG, "unbindAll error", t);
        }
        inferExec.shutdownNow();
    }

    @SuppressLint("UnsafeOptInUsageError")
    private void bindAnalysis() {
        if (cameraProvider == null) return;

        imageAnalysis = new ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST) // 只保留最新帧
                .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_YUV_420_888)
                .setTargetResolution(new Size(640,480))
                .build();

        imageAnalysis.setAnalyzer(inferExec, image -> {
            try {
                analyzeImage(image);
            } catch (Throwable t) {
                if (listener != null) listener.onError(t);
                Log.e(TAG, "analyzeImage error", t);
            } finally {
                image.close();
            }
        });

        CameraSelector cameraSelector = new CameraSelector.Builder()
                .requireLensFacing(CameraSelector.LENS_FACING_BACK)
                .build();

        cameraProvider.unbindAll();
        cameraProvider.bindToLifecycle(lifecycleOwner, cameraSelector, imageAnalysis);
    }

    @SuppressLint("UnsafeOptInUsageError")
    private void analyzeImage(@NonNull ImageProxy image) {
        if (skipFrames > 0) { skipFrames--; return; }

        long now = System.currentTimeMillis();
        if (now - lastProcessTs < PROCESS_GAP_MS) return;
        lastProcessTs = now;

        if (!busy.compareAndSet(false, true)) return;

        try {
            // YUV_420_888 -> NV21
            int w = image.getWidth();
            int h = image.getHeight();
            byte[] nv21 = yuv420888ToNv21(image);

            // 可选 180° 旋转（你的硬件镜头装反）
            if (rotate180) {
                nv21 = rotateNV21_180(nv21, w, h);
            }

            // 推理
            final FaceInfo faceInfo = faceEngine.detectFace(nv21, w, h);
            if (faceInfo == null) {
                if (listener != null) listener.onNoFace();
                return;
            }

            // 方向信息（可选）
            float offX = faceInfo.offsetXNorm;
            String dir = Math.abs(offX) < 0.05f ? "居中"
                    : (offX > 0 ? String.format(Locale.CHINA, "偏右 %.1f%%", Math.abs(offX) * 100f)
                    : String.format(Locale.CHINA, "偏左 %.1f%%", Math.abs(offX) * 100f));
            if (listener != null) listener.onFaceDirection(dir, offX);

            // 特征
            float[] feat = faceEngine.getFeature(nv21, w, h, faceInfo);
            if (feat == null || feat.length == 0) {
                if (listener != null) listener.onRecognizedLog("未获取到特征，跳过");
                return;
            }
            if (listener != null) listener.onFeature(feat);

            // 交给你已有的比对逻辑（入库、粘滞、播报策略等由你控制）
            Log.i(TAG, "特征长度=" + feat.length + ", sample=" + (feat.length > 8 ? Arrays.toString(Arrays.copyOf(feat, 8)) + "..." : Arrays.toString(feat)));
            FaceCompare.getInstance().faceCompareAction(feat, faceDb);

        } finally {
            busy.set(false);
        }
    }

    /** 将 YUV_420_888 转为紧凑 NV21 */
    private static byte[] yuv420888ToNv21(@NonNull ImageProxy image) {
        final ImageProxy.PlaneProxy[] planes = image.getPlanes();
        final ByteBuffer yBuf = planes[0].getBuffer();
        final ByteBuffer uBuf = planes[1].getBuffer();
        final ByteBuffer vBuf = planes[2].getBuffer();

        final int ySize = yBuf.remaining();
        final int uSize = uBuf.remaining();
        final int vSize = vBuf.remaining();

        // NV21 = Y + VU 交错
        byte[] out = new byte[ySize + uSize + vSize];

        // 拷贝 Y
        yBuf.get(out, 0, ySize);

        // Android 的 YUV_420_888 平面顺序是 Y U V，但 NV21 需要 VU
        // 需要把 V 放在前，U 放在后： [Y][V][U]
        // 注意某些设备 U/V 的排列/stride 会不同，下面的方式已足够应对常见机型
        byte[] uBytes = new byte[uSize];
        byte[] vBytes = new byte[vSize];
        uBuf.get(uBytes);
        vBuf.get(vBytes);

        // 交错 VU
        int chromaDst = ySize;
        int len = Math.min(uBytes.length, vBytes.length);
        for (int i = 0; i < len; i += 1) {
            out[chromaDst++] = vBytes[i];
            out[chromaDst++] = uBytes[i];
        }
        return out;
    }

    /** NV21 180° 旋转 */
    public static byte[] rotateNV21_180(byte[] nv21, int w, int h) {
        if (nv21 == null) return null;
        final int frameSize = w * h;
        final int chromaSize = frameSize / 2;
        if (nv21.length < frameSize + chromaSize) return nv21;

        byte[] out = new byte[frameSize + chromaSize];

        // Y 倒序
        for (int i = 0; i < frameSize; i++) {
            out[i] = nv21[frameSize - 1 - i];
        }
        // VU 成对倒序
        int dst = frameSize;
        for (int i = chromaSize - 2; i >= 0; i -= 2) {
            out[dst++] = nv21[frameSize + i];     // V
            out[dst++] = nv21[frameSize + i + 1]; // U
        }
        return out;
    }
}
