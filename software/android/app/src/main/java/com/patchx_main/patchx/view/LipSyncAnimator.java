package com.patchx_main.patchx.view;

import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.ViewPropertyAnimator;
import android.widget.ImageView;

import java.util.Locale;

public class LipSyncAnimator {

    private static final String TAG = "LipSyncAnimator";

    private final ImageView mouthView;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    // === 可调参数 ===
    private static final float SILENCE_THRESHOLD = 0.05f;   // 判定静音阈值
    private static final float HYSTERESIS = 0.02f;          // 抖动抑制
    private static final int   FADE_MS = 80;                // 图片淡入
    private static final int   SWITCH_INTERVAL_MS = 300;    // 0.3s 切换
    private static final int   SILENCE_FALLBACK_MS = 160;   // 尾音回落到 sil
    private static final float MAX_SCALE_BONUS = 0.35f;     // 最大缩放增幅（基于强度）
    private static final float MAX_Y_OFFSET   = 6f;         // 最大下移像素（模拟下颌）
    private static final int   POP_MS = 90;                 // 爆破辅音的“弹一下”动画时长
    // =================

    private int lastIndex = -1;
    private float lastValue = 0f;
    private long lastActiveTs = 0L;
    private long lastSwitchTs = 0L;
    private ViewPropertyAnimator currentFadeAnim, currentTransformAnim;

    // 15 个嘴形（与 OVRLipSync 一致，小写）
    private static final String[] VISEMES = {
            "sil", "pp", "ff", "th", "dd", "kk", "ch", "ss", "nn",
            "rr", "aa", "e", "ih", "oh", "ou"
    };

    // 不同嘴形的“张口权重”，越大张得越开（用于缩放/下移，经验值可按美术调整）
    // 索引与 VISEMES 一一对应
    private static final float[] OPEN_WEIGHTS = {
            0.00f, // sil  闭口
            0.05f, // pp   双唇闭合后瞬开（爆破用额外动画体现）
            0.08f, // ff   下唇触上齿，稍开
            0.10f, // th   舌尖齿间
            0.06f, // dd   轻度开口（爆破）
            0.08f, // kk   轻度开口（爆破）
            0.10f, // ch   轻度开口（塞擦音）
            0.07f, // ss   齿间缝隙
            0.06f, // nn   轻闭
            0.08f, // rr   嘴唇略收
            1.00f, // aa   大张口
            0.55f, // e    中开
            0.45f, // ih   中小开
            0.85f, // oh   圆唇较大开
            0.70f  // ou   圆唇中等开
    };

    // 哪些嘴形算“爆破/弹性”嘴形，给个迅速弹一下的反馈
    private static boolean isPopViseme(int idx) {
        return idx == 1 /*pp*/ || idx == 4 /*dd*/ || idx == 5 /*kk*/ || idx == 6 /*ch*/;
    }

    public LipSyncAnimator(ImageView mouthView) {
        this.mouthView = mouthView;
        // 以图片中心为缩放基准
        mouthView.setPivotX(mouthView.getWidth() * 0.5f);
        mouthView.setPivotY(mouthView.getHeight() * 0.5f);
    }

    /**
     * 根据 viseme 数组更新嘴形图像（带动画、静音、节流、缩放/位移/爆破）
     */
    public void updateMouth(float[] vis) {
        if (vis == null || vis.length != 15) return;

        // 取最大值和索引
        int maxIndex = 0;
        float maxValue = 0f;
        for (int i = 0; i < vis.length; i++) {
            if (vis[i] > maxValue) {
                maxValue = vis[i];
                maxIndex = i;
            }
        }

        long now = System.currentTimeMillis();

        // 活动时间 vs 尾音回落到 sil
        if (maxValue >= SILENCE_THRESHOLD) {
            lastActiveTs = now;
        } else if (now - lastActiveTs > SILENCE_FALLBACK_MS) {
            maxIndex = 0; // sil
        }

        // 抖动抑制
        if (maxIndex == lastIndex && Math.abs(maxValue - lastValue) < HYSTERESIS) {
            return;
        }

        // 0.3s 节流
//        if (now - lastSwitchTs < SWITCH_INTERVAL_MS) {
//            return;
//        }
        lastSwitchTs = now;

        lastIndex = maxIndex;
        lastValue = maxValue;

        final String resName = VISEMES[maxIndex].toLowerCase(Locale.US);
        final int showIndex = maxIndex;
        final float showStrength = clamp01(maxValue);

        mainHandler.post(() -> {
            setImageWithFade(resName);
            applyTransform(showIndex, showStrength);
        });
    }

    /**
     * 播放结束或需要强制闭嘴时调用
     */
    public void forceSilence() {
        lastIndex = 0;
        lastValue = 0f;
        lastActiveTs = 0L;
        lastSwitchTs = 0L;
        mainHandler.post(() -> {
            setImageWithFade("sil");
            applyTransform(0, 0f);
        });
    }

    // ============ 内部逻辑 ============

    private void setImageWithFade(String resName) {
        try {
            int resId = mouthView.getContext().getResources()
                    .getIdentifier(resName, "drawable", mouthView.getContext().getPackageName());
            if (resId == 0) {
                Log.w(TAG, "未找到图片资源：" + resName);
                return;
            }

            if (currentFadeAnim != null) currentFadeAnim.cancel();

            mouthView.setAlpha(0.35f);
            mouthView.setImageResource(resId);
            currentFadeAnim = mouthView.animate()
                    .alpha(1f)
                    .setDuration(FADE_MS);

            Log.d(TAG, "显示嘴形：" + resName);
        } catch (Exception e) {
            Log.e(TAG, "更新嘴形出错：" + e.getMessage());
        }
    }

    /**
     * 根据嘴形类型 & 强度，做缩放 + 下移（模拟下颌），并给爆破嘴形一个“弹一下”的感觉
     */
    private void applyTransform(int visemeIndex, float strength) {
        if (currentTransformAnim != null) currentTransformAnim.cancel();

        // 基础缩放：1.0 ~ 1.0 + MAX_SCALE_BONUS*openWeight*strength
        float openWeight = OPEN_WEIGHTS[visemeIndex];
        float targetScale = 1.0f + (MAX_SCALE_BONUS * openWeight * strength);
        targetScale = clamp(targetScale, 0.90f, 1.35f);

        // Y 方向位移：往下移，模拟张口下颌下降
        float targetTransY = MAX_Y_OFFSET * openWeight * strength;

        // 先做平滑过渡
        mouthView.animate()
                .scaleX(targetScale)
                .scaleY(targetScale)
                .translationY(targetTransY)
                .setDuration(FADE_MS)
                .start();

        // 爆破嘴形：给一个短促的“弹跳”效果（放大→回弹）
        if (isPopViseme(visemeIndex)) {
            float popScale = targetScale * 1.06f; // 稍稍大一点
            mouthView.setScaleX(popScale);
            mouthView.setScaleY(popScale);
            currentTransformAnim = mouthView.animate()
                    .scaleX(targetScale)
                    .scaleY(targetScale)
                    .setDuration(POP_MS);
        }
    }

    private static float clamp01(float v) {
        return v < 0f ? 0f : (v > 1f ? 1f : v);
    }

    private static float clamp(float v, float min, float max) {
        return Math.max(min, Math.min(max, v));
    }
}
