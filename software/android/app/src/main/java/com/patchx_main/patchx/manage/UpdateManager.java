package com.patchx_main.patchx.manage;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import androidx.annotation.Nullable;

import com.patchx_main.patchx.mqtt.ControllerEntity;
import com.patchx_main.patchx.update.DownloadHelper;
import com.wcy.testserial.FirmwareUpgradeManager;
import com.wcy.testserial.SerialPortUtil;

import java.io.File;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 串行的更新管理器：
 * - 同一时刻只允许一个下载在进行（MQTT 5s 推过来会被忽略）
 * - 成功完成后进入 5 分钟冷却期（卸载/安装窗口），冷却期内忽略新任务
 * - 失败/取消不进入冷却期，可立即重试
 *
 * 使用建议：
 *  1) 在 MqttManager 中持有一个全局单例 UpdateManager，不要每条消息 new。
 *  2) 优先调用 tryHandleUpdate(cmd)；返回 true 表示已接受并开始任务，false 表示忙/冷却而忽略。
 *  3) 若仍用老接口 handleUpdate(cmd)，在忙/冷却时会静默忽略，只打 onStep 提示。
 */
public class UpdateManager {

    public interface Listener {
        default void onStep(String tag) {}
        default void onProgress(String which, long cur, long total, float p) {}
        default void onDone(String which, File file) {}
        default void onError(String which, Throwable e) {}
        default void onAllFinished() {}
    }

    /** 成功后冷却时长（ms） */
    private static final long COOLDOWN_MS = 5 * 60_000L;

    private final Context appCtx;
    private final Listener cb;
    private final DownloadHelper downloader = new DownloadHelper();
    private final ExecutorService io = Executors.newFixedThreadPool(2);
    private final Handler main = new Handler(Looper.getMainLooper());

    // ==== 并发/重入控制 ====
    private final Object gate = new Object();
    /** 是否有下载在进行 */
    private boolean busy = false;
    /** 当前类型/URL（仅日志用） */
    private String busyWhich = null;
    private String busyUrl = null;
    /** 成功完成后的冷却截止时间戳（ms），<0 表示无冷却 */
    private long cooldownUntilMs = -1L;

    private volatile boolean cancelled = false;

    public UpdateManager(Context ctx, Listener listener) {
        this.appCtx = ctx.getApplicationContext();
        this.cb = listener;
    }

    /** 取消进行中的下载（若有），并释放 busy，不进入冷却期 */
    public void cancelAll() {
        cancelled = true;
        downloader.cancel();
        synchronized (gate) {
            busy = false;
            busyWhich = null;
            busyUrl = null;
            // 不改 cooldownUntilMs
        }
        fireStep("cancel requested");
        fireAllFinished();
    }

    /** 是否可接受新的更新任务（不 busy 且不在冷却期） */
    public boolean isAccepting() {
        synchronized (gate) {
            long now = System.currentTimeMillis();
            return !busy && !(cooldownUntilMs > 0 && now < cooldownUntilMs);
        }
    }

    /**
     * 推荐入口：尝试接单；仅当不 busy 且不在冷却期时才真正启动下载。
     * @return true=已接受并开始；false=忽略（忙/冷却/指令无效）。
     */
    public boolean tryHandleUpdate(@Nullable ControllerEntity cmd) {
        if (cmd == null || cmd.payload == null || isEmpty(cmd.payload.url)) {
            fireStep("invalid command or url");
            return false;
        }

        final String type = cmd.type;
        final String url  = cmd.payload.url;

        synchronized (gate) {
            long now = System.currentTimeMillis();
            if (cooldownUntilMs > 0 && now < cooldownUntilMs) {
                long left = (cooldownUntilMs - now + 999) / 1000;
                fireStep("skip update: in cooldown (" + left + "s left)");
                return false;
            }
            if (busy) {
                fireStep("skip update: downloading in progress (" + busyWhich + "), url=" + busyUrl);
                return false;
            }
            // 占位
            busy = true;
            busyWhich = type;
            busyUrl = url;
            cancelled = false;
        }

        // 真正启动（避免在锁内耗时）
        startInternal(cmd);
        return true;
    }

    /**
     * 兼容旧接口：在忙/冷却时会静默忽略（只打 onStep），不抛异常。
     * 如需知道是否接受，可改用 tryHandleUpdate()。
     */
    public void handleUpdate(@Nullable ControllerEntity cmd) {
        tryHandleUpdate(cmd); // 兼容调用
    }

    // ---------------- 内部核心：按类型执行下载/处理 ----------------
    private void startInternal(@Nullable ControllerEntity cmd) {
        if (cmd == null || cmd.payload == null || isEmpty(cmd.payload.url)) {
            finish(false);
            return;
        }
        final String type = cmd.type;
        final String url  = cmd.payload.url;
        final String ver  = cmd.payload.version;
        final long   expectSize = (cmd.payload.size != null) ? Math.max(0L, cmd.payload.size) : -1L;

        switch (type) {
            case ControllerEntity.TYPE_APP: {
                final String name = fileNameFromUrl(url, safeVerName(ver, "update") + ".apk");
                final File dst = DownloadHelper.apkFile(appCtx, name);
                downloadOne("app", url, dst, expectSize, () -> {
                    if (cancelled) { finish(false); return; }
//                    if (!checkSize("app", dst, expectSize)) { finish(false); return; }
                    onApkReady(dst, ver);
                    fireDone("app", dst);
                    finish(true); // 成功 -> 进入冷却
                });
                break;
            }
            case ControllerEntity.TYPE_FIRMWARE: {
                final String name = fileNameFromUrl(url, safeVerName(ver, "firmware") + ".bin");
                final File dst = DownloadHelper.firmwareBin(appCtx, name);
                downloadOne("firmware", url, dst, expectSize, () -> {
                    if (cancelled) { finish(false); return; }
//                    if (!checkSize("firmware", dst, expectSize)) { finish(false); return; }
                    try {
                        flashFirmwareOverSerial(dst);
                        fireDone("firmware", dst);
                        finish(true);
                    } catch (Throwable e) {
                        fireError("firmware", e);
                        finish(false);
                    }
                });
                break;
            }
            case ControllerEntity.TYPE_SYSTEM: {
                final String name = fileNameFromUrl(url, safeVerName(ver, "system") + ".zip");
                final File dst = DownloadHelper.systemZip(appCtx, name);
                downloadOne("system", url, dst, expectSize, () -> {
                    if (cancelled) { finish(false); return; }
                    if (!checkSize("system", dst, expectSize)) { finish(false); return; }
                    try {
                        applySystemPackage(dst, ver);
                        fireDone("system", dst);
                        finish(true);
                    } catch (Throwable e) {
                        fireError("system", e);
                        finish(false);
                    }
                });
                break;
            }
            default:
                fireError("type", new IllegalArgumentException("unknown type: " + type));
                finish(false);
        }
    }

    // ---------------- 下载封装 ----------------
    private void downloadOne(String which, String url, File dst, long expectSize, Runnable after) {
        fireStep("start " + which + " download");
        downloader.download(url, dst, new DownloadHelper.Listener() {
            @Override public void onProgress(String u, long cur, long total, float p) {
                if (!cancelled) fireProgress(which, cur, total > 0 ? total : expectSize, p);
            }
            @Override public void onSuccess(String u, File file) {
                if (cancelled) {
                    fireStep(which + " cancelled");
                    finish(false);
                    return;
                }
                fireStep(which + " downloaded: " + file.getAbsolutePath());
                io.execute(after);
            }
            @Override public void onError(String u, Throwable e) {
                fireError(which, e);
                finish(false); // 出错 -> 不进入冷却
            }
            @Override public void onCancelled(String u) {
                fireStep(which + " cancelled");
                finish(false); // 取消 -> 不进入冷却
            }
        });
    }

    private boolean checkSize(String which, File f, long expectSize) {
        if (expectSize > 0 && f.length() != expectSize) {
            fireError(which, new IllegalStateException(
                    "size mismatch, expect=" + expectSize + ", got=" + f.length()));
            return false;
        }
        return true;
    }

    /** APK 下载完成后的处理（沿用你现有逻辑） */
    private void onApkReady(File apk, @Nullable String version) {
        DeviceManager.getInstance(appCtx).updateMainApp(apk);
    }

    /** 串口下发固件（保持你之前的逻辑，可按需替换串口库） */
    private void flashFirmwareOverSerial(File bin) throws Exception {
        try{
            SerialPortUtil serialPortUtil = new SerialPortUtil();
            int result = serialPortUtil.open("/dev/ttySMT4", 115200);
            if (result != 0) {
                Log.e("Upgrade", "串口打开失败！");
                return;
            }
            FirmwareUpgradeManager manager = new FirmwareUpgradeManager(serialPortUtil);

            manager.sendFirmware(bin, new FirmwareUpgradeManager.FirmwareUpgradeCallback() {
                @Override
                public void onProgress(int current, int total) {
                    Log.i("Upgrade", "进度：" + current + " / " + total);
                }

                @Override
                public void onSuccess() {
                    Log.i("Upgrade", "升级成功！");
                }

                @Override
                public void onFailure(String error) {
                    Log.e("Upgrade", "升级失败: " + error);
                }
            });

        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    /** 应用系统包（预留） */
    private void applySystemPackage(File zip, @Nullable String version) throws Exception {
        fireStep("apply system package: " + zip.getAbsolutePath());
        // TODO: 调用系统更新 API
    }

    // ---------------- 工具 & 回调派发 ----------------
    private static boolean isEmpty(String s){ return s == null || s.trim().isEmpty(); }

    private static String safeVerName(String ver, String def) {
        if (isEmpty(ver)) return def;
        return ver.replaceAll("[^0-9A-Za-z._-]", "_");
    }

    private String fileNameFromUrl(String url, String def) {
        try {
            if (url == null || url.isEmpty()) return def;
            int q = url.indexOf('?');
            if (q >= 0 && q < url.length() - 1) {
                String query = url.substring(q + 1);
                for (String kv : query.split("&")) {
                    String[] pair = kv.split("=", 2);
                    if (pair.length == 2 && "object_key".equals(pair[0])) {
                        String val = pair[1];
                        int slash = val.lastIndexOf('/');
                        String name = (slash >= 0) ? val.substring(slash + 1) : val;
                        name = name.replaceAll("[\\\\/:*?\"<>|\\s]+", "_"); // 清理非法字符
                        if (!name.isEmpty()) return name;
                    }
                }
                url = url.substring(0, q);
            }
            int idx = url.lastIndexOf('/');
            if (idx >= 0 && idx < url.length() - 1) {
                String name = url.substring(idx + 1).trim();
                name = name.replaceAll("[\\\\/:*?\"<>|\\s]+", "_");
                if (!name.isEmpty()) return name;
            }
        } catch (Throwable ignore) {}
        return def;
    }

    private void fireStep(String s){ if (cb!=null) main.post(() -> cb.onStep(s)); }
    private void fireProgress(String w,long c,long t,float p){ if (cb!=null) main.post(() -> cb.onProgress(w,c,t,p)); }
    private void fireDone(String w, File f){ if (cb!=null) main.post(() -> cb.onDone(w,f)); }
    private void fireError(String w, Throwable e){ if (cb!=null) main.post(() -> cb.onError(w,e)); }
    private void fireAllFinished(){ if (cb!=null) main.post(cb::onAllFinished); }

    /** 统一收尾：success=true 时进入 5 分钟冷却；false 则不进入冷却 */
    private void finish(boolean success) {
        synchronized (gate) {
            busy = false;
            busyWhich = null;
            busyUrl = null;
            if (success) {
                cooldownUntilMs = System.currentTimeMillis() + COOLDOWN_MS;
            }
        }
        fireAllFinished();
    }
}
