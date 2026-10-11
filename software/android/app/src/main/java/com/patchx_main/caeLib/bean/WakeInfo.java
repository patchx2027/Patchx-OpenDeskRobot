package com.patchx_main.caeLib.bean;

public class WakeInfo {
    private int beam;
    private float score;
    private int angle;

    public int getBeam() {
        return beam;
    }

    public void setBeam(int beam) {
        this.beam = beam;
    }

    public float getScore() {
        return score;
    }

    public void setScore(float score) {
        this.score = score;
    }

    public int getAngle() {
        return angle;
    }

    public void setAngle(int angle) {
        this.angle = angle;
    }

    @Override
    public String toString() {
        return "WakeInfo{" +
                "beam=" + beam +
                ", score=" + score +
                ", angle=" + angle +
                '}';
    }
}
