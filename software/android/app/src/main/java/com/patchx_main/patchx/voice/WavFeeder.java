package com.patchx_main.patchx.voice;

import android.content.Context;
import android.content.res.AssetFileDescriptor;
import android.media.AudioTrack;
import android.net.Uri;
import android.os.SystemClock;
import android.text.TextUtils;
import android.util.Log;

import java.io.BufferedInputStream;
import java.io.DataInputStream;
import java.io.FileInputStream;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicLong;

/**
 * WavFeeder
 * - 空闲才播 + 冷却
 * - 自动转码到 PCM16 / MONO / sampleRate = AudioConfig.SAMPLE_RATE
 * - 播完可选自动 stop（默认 true）
 * - 提供：等待空闲再播 / 预占式提示音
 */
public final class WavFeeder {
    private static final String TAG = "WavFeeder";

    /** 每块目标播放时长（ms），影响喂入粒度与延迟 */
    private static final int TARGET_CHUNK_MS = 120;

    /** 默认冷却（ms）：冷却期内拒绝再次播放 */
    private static final long DEFAULT_COOLDOWN_MS = 2000L;

    /** 播完是否自动 stop（默认 true；若用于持续流可设为 false） */
    private static final boolean AUTO_STOP_AFTER_PLAY = true;

    private static final AtomicLong sLastPlayAtMs = new AtomicLong(0);

    private WavFeeder() {}

    /* ===================== 高级便捷接口 ===================== */

    /** 最多等待 waitMs 让 AudioPlayer 空闲，然后常规播放（含冷却&转码&自动stop） */
    public static boolean playWavWhenIdle(Context ctx, String assetName, long waitMs) {
        if (ctx == null || TextUtils.isEmpty(assetName)) return false;
        waitUntilIdle(waitMs);
        return tryPlayWavIfIdleFromAssets(ctx, assetName);
    }

    /** 预占式提示音：先短暂停当前流 → 播提示音（含冷却&转码&自动stop） */
    public static boolean playPromptPreemptFromAssets(Context ctx, String assetName) {
        if (ctx == null || TextUtils.isEmpty(assetName)) return false;
        if (!passCooldown(DEFAULT_COOLDOWN_MS)) return false;
        try { AudioPlayer.getInstance().stopPlayback(); } catch (Exception ignore) {}
        SystemClock.sleep(20); // 给 AudioTrack 状态切换时间
        playWavFromAssets(ctx, assetName, /*autoStop*/true);
        return true;
    }

    /* ===================== 常规入口（忙则不播 + 冷却） ===================== */

    public static boolean tryPlayWavIfIdle(String path) {
        if (!isIdle() || !passCooldown(DEFAULT_COOLDOWN_MS) || TextUtils.isEmpty(path)) return false;
        playWavFromFile(path, AUTO_STOP_AFTER_PLAY);
        return true;
    }

    public static boolean tryPlayWavIfIdle(Context ctx, Uri uri) {
        if (!isIdle() || !passCooldown(DEFAULT_COOLDOWN_MS) || ctx == null || uri == null) return false;
        playWavFromUri(ctx, uri, AUTO_STOP_AFTER_PLAY);
        return true;
    }

    public static boolean tryPlayWavIfIdleFromAssets(Context ctx, String assetName) {
        if (!isIdle() || !passCooldown(DEFAULT_COOLDOWN_MS) || ctx == null || TextUtils.isEmpty(assetName)) return false;
        playWavFromAssets(ctx, assetName, AUTO_STOP_AFTER_PLAY);
        return true;
    }

    public static boolean tryPlayWavIfIdleFromRaw(Context ctx, int rawResId) {
        if (!isIdle() || !passCooldown(DEFAULT_COOLDOWN_MS) || ctx == null || rawResId == 0) return false;
        playWavFromRaw(ctx, rawResId, AUTO_STOP_AFTER_PLAY);
        return true;
    }

    /** 自定义冷却版本 */
    public static boolean tryPlayWavIfIdleWithCooldown(Context ctx, Uri uri, long cooldownMs) {
        if (!isIdle() || !passCooldown(Math.max(0, cooldownMs)) || ctx == null || uri == null) return false;
        playWavFromUri(ctx, uri, AUTO_STOP_AFTER_PLAY);
        return true;
    }

    /* ===================== 实际播放入口（支持选择是否自动 stop） ===================== */

    public static void playWavFromFile(String path, boolean autoStop) {
        try (FileInputStream fis = new FileInputStream(path)) {
            feed(new BufferedInputStream(fis), autoStop);
        } catch (Exception e) {
            Log.e(TAG, "playWavFromFile error: " + e.getMessage(), e);
        }
    }

    public static void playWavFromUri(Context ctx, Uri uri, boolean autoStop) {
        try (InputStream is = ctx.getContentResolver().openInputStream(uri)) {
            if (is == null) return;
            feed(new BufferedInputStream(is), autoStop);
        } catch (Exception e) {
            Log.e(TAG, "playWavFromUri error: " + e.getMessage(), e);
        }
    }

    public static void playWavFromAssets(Context ctx, String assetName, boolean autoStop) {
        try (InputStream is = ctx.getAssets().open(assetName)) {
            feed(new BufferedInputStream(is), autoStop);
        } catch (Exception e) {
            Log.e(TAG, "playWavFromAssets error: " + e.getMessage(), e);
        }
    }

    public static void playWavFromRaw(Context ctx, int rawResId, boolean autoStop) {
        try (AssetFileDescriptor afd = ctx.getResources().openRawResourceFd(rawResId)) {
            if (afd == null) return;
            try (InputStream is = afd.createInputStream()) {
                feed(new BufferedInputStream(is), autoStop);
            }
        } catch (Exception e) {
            Log.e(TAG, "playWavFromRaw error: " + e.getMessage(), e);
        }
    }

    /* ===================== WAV 解析 & 转码 & 分块喂入 ===================== */

    private static void feed(InputStream is, boolean autoStop) throws Exception {
        // 解析 WAV 头
        WavInfo info = parseWavHeader(is);
        if (info.dataSize <= 0) return;

        final int targetRate = AudioConfig.SAMPLE_RATE;
        final int srcBytesPerFrame = (info.bitsPerSample / 8) * info.numChannels;

        // 以目标 120ms 为参考，估算“源帧”块大小
        int targetSamplesPerChunk = Math.max(1, targetRate * TARGET_CHUNK_MS / 1000);
        int srcFramesPerChunk = Math.max(1, (int) ((long) targetSamplesPerChunk * info.sampleRate / targetRate));
        int srcBytesPerChunk = Math.min(info.dataSize, srcFramesPerChunk * srcBytesPerFrame);
        byte[] srcBuf = new byte[Math.max(srcBytesPerChunk, 4096)];

        AudioPlayer player = AudioPlayer.getInstance();
        long totalTargetFrames = 0; // 记录目标域写入的帧数（mono 时 = 样本数）

        int remaining = info.dataSize;
        while (remaining > 0) {
            int toRead = Math.min(remaining, srcBuf.length);
            int n = readFully(is, srcBuf, 0, toRead);
            if (n <= 0) break;
            remaining -= n;

            int frames = n / srcBytesPerFrame;
            if (frames <= 0) continue;

            // 1) PCM -> float[-1,1]（多声道交错）
            float[] interleaved = decodePcmToFloat(srcBuf, frames, info.numChannels, info.bitsPerSample);

            // 2) 下混为单声道
            float[] mono = (info.numChannels == 1) ? interleaved : downmixToMono(interleaved, frames, info.numChannels);

            // 3) 重采样至目标采样率
            float[] resampled = (info.sampleRate == targetRate) ? mono
                    : resampleLinear(mono, info.sampleRate, targetRate);

            // 4) float -> PCM16
            short[] pcm16 = floatToPcm16(resampled);

            // 5) 写入 AudioPlayer
            player.playAudio(pcm16);
            totalTargetFrames += pcm16.length; // mono: 1 sample = 1 frame
        }

        // 播完后的收尾：等待缓冲播完（或到上限），再决定是否 stop
        if (autoStop) {
            safeDrainAndStop(totalTargetFrames, targetRate);
        }
    }

    /* ===================== 播放完成等待 & 停止 ===================== */

    /**
     * 等待播放头消化我们写入的帧（设上限），随后调用 AudioPlayer.stopPlayback()。
     * 注：AudioPlayer 内你若启用了看门狗，也会在空闲时自动 stop；这里是主动收尾，保证下次可再播。
     */
    private static void safeDrainAndStop(long totalFramesWritten, int sampleRate) {
        try {
            AudioTrack at = AudioPlayer.getInstance().getAudioTrack();
            if (at != null && totalFramesWritten > 0 && sampleRate > 0) {
                long estimateMs = Math.max(0, (totalFramesWritten * 1000L / sampleRate) - 5);
                long waitCapMs = Math.min(estimateMs + 80, 2000); // 上限避免死等
                long start = SystemClock.elapsedRealtime();
                int last = at.getPlaybackHeadPosition();

                while (SystemClock.elapsedRealtime() - start < waitCapMs) {
                    int pos = at.getPlaybackHeadPosition();
                    if ((long) pos >= totalFramesWritten) break; // 播完
                    if (pos == last) break;                      // 不再前进，视为播空
                    last = pos;
                    Thread.sleep(10);
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "safeDrainAndStop wait error: " + e.getMessage());
        } finally {
            try { AudioPlayer.getInstance().stopPlayback(); } catch (Exception ignore) {}
        }
    }

    /* ===================== WAV 解析 ===================== */

    private static class WavInfo {
        int sampleRate;
        int numChannels;
        int bitsPerSample; // 8/16/24/32
        int dataSize;      // bytes
    }

    private static WavInfo parseWavHeader(InputStream is) throws Exception {
        DataInputStream in = new DataInputStream(is);

        byte[] riff = new byte[12];
        in.readFully(riff);
        if (!fourCCEquals(riff, 0, "RIFF") || !fourCCEquals(riff, 8, "WAVE")) {
            throw new IllegalArgumentException("Not a RIFF/WAVE file");
        }

        WavInfo wi = new WavInfo();
        boolean gotFmt = false, gotData = false;

        while (true) {
            byte[] chunkHdr = new byte[8];
            if (in.read(chunkHdr) < 8) throw new IllegalArgumentException("Unexpected EOF before 'data'");
            String id = new String(chunkHdr, 0, 4, StandardCharsets.US_ASCII);
            int size = le32(chunkHdr, 4);

            if ("fmt ".equals(id)) {
                byte[] fmt = new byte[size];
                in.readFully(fmt);

                int audioFormat   = le16(fmt, 0); // 1 = PCM
                wi.numChannels    = le16(fmt, 2);
                wi.sampleRate     = le32(fmt, 4);
                wi.bitsPerSample  = le16(fmt, 14);

                if (audioFormat != 1)
                    throw new IllegalArgumentException("Only PCM (format 1) is supported");
                if (wi.numChannels < 1 || wi.numChannels > 2)
                    throw new IllegalArgumentException("Channels not supported: " + wi.numChannels);
                if (wi.bitsPerSample != 8 && wi.bitsPerSample != 16 &&
                        wi.bitsPerSample != 24 && wi.bitsPerSample != 32)
                    throw new IllegalArgumentException("Bit depth not supported: " + wi.bitsPerSample);

                gotFmt = true;
            } else if ("data".equals(id)) {
                wi.dataSize = size;
                gotData = true;
                break;
            } else {
                long skip = size + (size & 1); // 2字节对齐
                long skipped = in.skip(skip);
                if (skipped != skip) throw new IllegalArgumentException("Skip chunk failed: " + id);
            }
        }

        if (!gotFmt || !gotData) throw new IllegalArgumentException("Invalid WAV: missing fmt/data");
        return wi;
    }

    /* ===================== 解码 / 转换 ===================== */

    // PCM（8/16/24/32-bit LE，多声道交错） -> float[-1,1] 交错
    private static float[] decodePcmToFloat(byte[] src, int frames, int ch, int bits) {
        float[] out = new float[frames * ch];
        ByteBuffer bb = ByteBuffer.wrap(src, 0, frames * (bits / 8) * ch).order(ByteOrder.LITTLE_ENDIAN);

        switch (bits) {
            case 8: { // 8-bit PCM 常见无符号：0..255 -> -1..1
                for (int i = 0; i < out.length; i++) {
                    int u = bb.get() & 0xFF;
                    out[i] = (u - 128) / 128f;
                }
                break;
            }
            case 16: {
                for (int i = 0; i < out.length; i++) {
                    out[i] = bb.getShort() / 32768f;
                }
                break;
            }
            case 24: { // 24-bit 有符号 LE
                for (int i = 0; i < out.length; i++) {
                    int b0 = bb.get() & 0xFF;
                    int b1 = bb.get() & 0xFF;
                    int b2 = bb.get(); // signed
                    int v = (b2 << 16) | (b1 << 8) | b0;
                    if ((v & 0x00800000) != 0) v |= 0xFF000000; // 符号扩展
                    out[i] = clamp01(v / 8388608f); // 2^23
                }
                break;
            }
            case 32: { // 假设 32-bit 整型 PCM
                for (int i = 0; i < out.length; i++) {
                    int v = bb.getInt();
                    out[i] = clamp01(v / 2147483648f); // 2^31
                }
                break;
            }
            default:
                throw new IllegalArgumentException("Unsupported bits: " + bits);
        }
        return out;
    }

    // 多声道交错 -> 单声道（平均）
    private static float[] downmixToMono(float[] interleaved, int frames, int ch) {
        float[] mono = new float[frames];
        if (ch == 2) {
            for (int f = 0, i = 0; f < frames; f++, i += 2) {
                mono[f] = 0.5f * (interleaved[i] + interleaved[i + 1]);
            }
        } else {
            for (int f = 0; f < frames; f++) {
                float acc = 0;
                for (int c = 0; c < ch; c++) acc += interleaved[f * ch + c];
                mono[f] = acc / ch;
            }
        }
        return mono;
    }

    // 线性插值重采样（单声道 float）
    private static float[] resampleLinear(float[] in, int inRate, int outRate) {
        if (inRate == outRate) return in;
        int outLen = (int) ((long) in.length * outRate / inRate);
        if (outLen <= 1) return new float[0];
        float[] out = new float[outLen];
        double step = (double) (in.length - 1) / (outLen - 1); // 两端对齐
        for (int i = 0; i < outLen; i++) {
            double src = i * step;
            int i0 = (int) src;
            int i1 = Math.min(i0 + 1, in.length - 1);
            double t = src - i0;
            out[i] = (float) ((1.0 - t) * in[i0] + t * in[i1]);
        }
        return out;
    }

    // float[-1,1] -> PCM16
    private static short[] floatToPcm16(float[] in) {
        short[] out = new short[in.length];
        for (int i = 0; i < in.length; i++) {
            float v = Math.max(-1f, Math.min(1f, in[i]));
            out[i] = (short) Math.round(v * 32767f);
        }
        return out;
    }

    private static float clamp01(float v) {
        if (v > 1f) return 1f;
        if (v < -1f) return -1f;
        return v;
    }

    /* ===================== 工具 & 状态判断 ===================== */

    private static void waitUntilIdle(long waitMs) {
        waitMs = Math.max(0, waitMs);
        AudioTrack at = AudioPlayer.getInstance().getAudioTrack();
        if (at == null) return;
        int lastPos = at.getPlaybackHeadPosition();
        long start = SystemClock.elapsedRealtime();
        while (SystemClock.elapsedRealtime() - start < waitMs) {
            int state = at.getPlayState();
            int pos = at.getPlaybackHeadPosition();
            if (state != AudioTrack.PLAYSTATE_PLAYING) break; // 已停
            if (pos == lastPos) break;                        // 头不再前进，视为空闲
            lastPos = pos;
            try { Thread.sleep(30); } catch (InterruptedException ignored) {}
        }
    }

    /** 空闲判定：不在 PLAYING 或播放头不再前进 即视为空闲（必要时主动 stop 一次以释放状态） */
    private static boolean isIdle() {
        AudioTrack at = AudioPlayer.getInstance().getAudioTrack();
        if (at == null) return true;
        if (at.getPlayState() != AudioTrack.PLAYSTATE_PLAYING) return true;

        int last = at.getPlaybackHeadPosition();
        try { Thread.sleep(40); } catch (InterruptedException ignored) {}
        int now = at.getPlaybackHeadPosition();
        if (now == last) {
            // 缓冲可能已空但状态未切回 -> 主动停一次，避免一直“忙”
            try { AudioPlayer.getInstance().stopPlayback(); } catch (Exception ignore) {}
            return true;
        }
        return false; // 还在前进，确实在播
    }

    private static boolean passCooldown(long cooldownMs) {
        long now = SystemClock.elapsedRealtime();
        long last = sLastPlayAtMs.get();
        if (now - last < cooldownMs) return false;
        return sLastPlayAtMs.compareAndSet(last, now);
    }

    private static int readFully(InputStream is, byte[] buf, int off, int len) throws Exception {
        int total = 0;
        while (total < len) {
            int n = is.read(buf, off + total, len - total);
            if (n <= 0) break;
            total += n;
        }
        return total;
    }

    private static boolean fourCCEquals(byte[] b, int off, String fourCC) {
        return fourCC.equals(new String(b, off, 4, StandardCharsets.US_ASCII));
    }

    private static int le16(byte[] b, int off) {
        return (b[off] & 0xFF) | ((b[off + 1] & 0xFF) << 8);
    }

    private static int le32(byte[] b, int off) {
        return (b[off] & 0xFF) | ((b[off + 1] & 0xFF) << 8) | ((b[off + 2] & 0xFF) << 16) | ((b[off + 3] & 0xFF) << 24);
    }
}
