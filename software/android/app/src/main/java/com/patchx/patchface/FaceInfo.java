package com.patchx.patchface;

public class FaceInfo {
    public int x;
    public int y;
    public int w;
    public int h;
    public float score;

    // 新增
    public int cx;            // 人脸中心(模型输入坐标系：gInW x gInH)
    public int cy;
    public float offsetXNorm; // 相对中心的水平归一化偏移，[-1,1]，右正左负
    public float offsetYNorm; // 垂直归一化偏移，[-1,1]，下正上负

    public FaceInfo(int x, int y, int w, int h, float score,
                    int cx, int cy, float offsetXNorm, float offsetYNorm) {
        this.x = x; this.y = y; this.w = w; this.h = h; this.score = score;
        this.cx = cx; this.cy = cy; this.offsetXNorm = offsetXNorm; this.offsetYNorm = offsetYNorm;
    }

    public String horiBias() {
        if (offsetXNorm >  0.05f) return "偏右 " + String.format("%.1f%%", offsetXNorm*100f);
        if (offsetXNorm < -0.05f) return "偏左 " + String.format("%.1f%%", -offsetXNorm*100f);
        return "居中";
    }
    public String vertBias() {
        if (offsetYNorm >  0.05f) return "偏下 " + String.format("%.1f%%", offsetYNorm*100f);
        if (offsetYNorm < -0.05f) return "偏上 " + String.format("%.1f%%", -offsetYNorm*100f);
        return "居中";
    }

    @Override
    public String toString() {
        return "FaceInfo{x=" + x + ", y=" + y + ", w=" + w + ", h=" + h +
                ", score=" + score + ", cx=" + cx + ", cy=" + cy +
                ", offX=" + offsetXNorm + ", offY=" + offsetYNorm + "}";
    }
}
