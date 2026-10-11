package com.patchx_main.patchx.update;

import android.content.Context;

import androidx.annotation.Nullable;

import java.io.File;
import java.io.FileOutputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import okhttp3.Call;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;

/** 极简下载器：一次性整包下载（无续传/无校验），带进度/取消回调 */
public class DownloadHelper {

    public interface Listener {
        /** 0.0~1.0，若 totalBytes=-1 则 progress 可能为 -1 表示未知 */
        default void onProgress(String url, long downloadedBytes, long totalBytes, float progress) {}
        /** 成功（文件已写完并关闭） */
        default void onSuccess(String url, File file) {}
        /** 失败 */
        default void onError(String url, Throwable e) {}
        /** 被取消 */
        default void onCancelled(String url) {}
    }

    private final OkHttpClient http = new OkHttpClient.Builder()
            .retryOnConnectionFailure(true)
            .build();
    private final ExecutorService io = Executors.newSingleThreadExecutor();

    /** 取消用：保存最后一次 call（单任务） */
    private volatile Call currentCall;
    private volatile boolean cancelled = false;

    public void cancel() {
        cancelled = true;
        Call c = currentCall;
        if (c != null) c.cancel();
    }

    /** APP 专属下载目录：/Android/data/<pkg>/files/downloads */
    public static File appDownloadsDir(Context ctx) {
        File dir = new File(ctx.getExternalFilesDir(null), "downloads");
        if (!dir.exists()) dir.mkdirs();
        return dir;
    }

    /** APK 保存路径 */
    public static File apkFile(Context ctx, @Nullable String fileName) {
        return new File(appDownloadsDir(ctx),
                (fileName == null || fileName.isEmpty()) ? "app-release.apk" : fileName);
    }

    /** 固件 BIN 保存路径 */
    public static File firmwareBin(Context ctx, @Nullable String fileName) {
        return new File(appDownloadsDir(ctx),
                (fileName == null || fileName.isEmpty()) ? "firmware.bin" : fileName);
    }

    /** 系统 ZIP 保存路径 */
    public static File systemZip(Context ctx, @Nullable String fileName) {
        return new File(appDownloadsDir(ctx),
                (fileName == null || fileName.isEmpty()) ? "system.zip" : fileName);
    }

    /**
     * 简单整包下载（覆盖式）。
     * @param url 下载地址
     * @param dst 目标文件（若已存在会先删除）
     * @param cb  回调
     */
    public void download(String url, File dst, @Nullable Listener cb) {
        cancelled = false;

        io.execute(() -> {
            Response rsp = null;
            FileOutputStream out = null;
            try {
                // 准备目标文件
                if (dst.exists() && !dst.delete()) {
                    throw new RuntimeException("delete old file fail: " + dst);
                }
                if (!dst.getParentFile().exists() && !dst.getParentFile().mkdirs()) {
                    throw new RuntimeException("mkdirs fail: " + dst.getParent());
                }

                Request req = new Request.Builder()
                        .url(url)
                        .header("Accept-Encoding", "identity") // 禁止压缩，便于精确进度
                        .header("User-Agent", "PatchX-Updater/1.0")
                        .build();

                currentCall = http.newCall(req);
                rsp = currentCall.execute();

                if (cancelled) { if (cb != null) cb.onCancelled(url); return; }

                if (!rsp.isSuccessful() || rsp.body() == null) {
                    throw new RuntimeException("HTTP " + (rsp != null ? rsp.code() : -1));
                }

                long total = -1;
                try {
                    String len = rsp.header("Content-Length");
                    if (len != null) total = Long.parseLong(len);
                } catch (Throwable ignore) {}

                out = new FileOutputStream(dst);
                byte[] buf = new byte[8192];
                long downloaded = 0, lastEmit = System.currentTimeMillis();

                try (var in = rsp.body().byteStream()) {
                    int n;
                    while ((n = in.read(buf)) != -1) {
                        if (cancelled) { if (cb != null) cb.onCancelled(url); return; }
                        out.write(buf, 0, n);
                        downloaded += n;

                        long now = System.currentTimeMillis();
                        if (cb != null && now - lastEmit >= 100) { // 100ms节流
                            cb.onProgress(url, downloaded, total, total > 0 ? downloaded * 1f / total : -1f);
                            lastEmit = now;
                        }
                    }
                }
                out.getFD().sync();

                // 最后一帧进度
                if (cb != null) cb.onProgress(url, downloaded, total, total > 0 ? 1f : 1f);
                if (cb != null) cb.onSuccess(url, dst);
            } catch (Throwable e) {
                if (cb != null) cb.onError(url, e);
            } finally {
                try { if (rsp != null) rsp.close(); } catch (Throwable ignore) {}
                try { if (out != null) out.close(); } catch (Throwable ignore) {}
                currentCall = null;
            }
        });
    }
}
