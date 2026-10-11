package com.patchx_main.patchx.voice;

import android.util.Log;
import java.util.Arrays;

/** RMS+FFT → 15 维 viseme（float[]），温和 Hysteresis、去 0.55 量化、Vowel Rebalance、擦音门控、详细日志 */
public class VisemeEstimator {

    private static final String TAG = "VisemeEstimator";
    private boolean logEnabled = false;
    private int     logThrottle = 3;  // 每多少帧打印一次
    private int     frameCount = 0;

    // 0:SIL,1:PP,2:FF,3:TH,4:DD,5:KK,6:CH,7:SS,8:AA,9:E,10:I,11:O,12:U,13:RR,14:SIL2
    private static final String[] V_NAMES = {
            "SIL","PP","FF","TH","DD","KK","CH","SS","AA","E","I","O","U","RR","SIL2"
    };

    // —— 简易 FFT —— //
    private static final class SimpleFFT {
        final int n, log2n;
        final double[] cos, sin, real, imag;
        SimpleFFT(int n) {
            if (Integer.bitCount(n) != 1) throw new IllegalArgumentException("n must be power of 2");
            this.n = n; this.log2n = Integer.numberOfTrailingZeros(n);
            this.cos = new double[n/2]; this.sin = new double[n/2];
            for (int i=0;i<n/2;i++){ double a=-2*Math.PI*i/n; cos[i]=Math.cos(a); sin[i]=Math.sin(a); }
            this.real = new double[n]; this.imag = new double[n];
        }
        private static int br(int x,int b){int y=0;for(int i=0;i<b;i++){y=(y<<1)|(x&1);x>>>=1;}return y;}
        void fftReal(double[] in,double[] outMag){
            int N=n,bins=N/2,step;
            for(int i=0;i<N;i++){int j=br(i,log2n);real[j]=in[i];imag[j]=0;}
            for(int size=2;size<=N;size<<=1){
                int half=size>>1; step=N/size;
                for(int i=0;i<N;i+=size){
                    for(int j=0;j<half;j++){
                        double wr=cos[j*step], wi=sin[j*step];
                        int i0=i+j, i1=i+j+half;
                        double xr=real[i1]*wr - imag[i1]*wi;
                        double xi=real[i1]*wi + imag[i1]*wr;
                        real[i1]=real[i0]-xr; imag[i1]=imag[i0]-xi;
                        real[i0]+=xr;        imag[i0]+=xi;
                    }
                }
            }
            outMag[0]=Math.hypot(real[0],imag[0])/N;
            for(int k=1;k<bins;k++) outMag[k]=2.0*Math.hypot(real[k],imag[k])/N;
            outMag[bins]=Math.hypot(real[bins],imag[bins])/N;
        }
    }

    // —— 状态 —— //
    private int lastN = 0;
    private SimpleFFT fft;
    private double[] window, timeBuf, mag;

    // 几何中间量的 EMA：open/round/width/lip/teeth/tongue
    private final float[] smoothed = new float[6];
    private float ema = 0.50f;  // 越大越灵敏

    // 擦音提示 EMA
    private float fricEma = 0f;

    // 粘滞：上一帧 Top
    private int   prevTop = 0;
    private float prevTopVal = 0f;
    private int   holdFrames = 0;

    // ===== 公共设置 =====
    public void setLogEnabled(boolean enabled) { this.logEnabled = enabled; }
    public void setLogThrottle(int everyNFrames) { this.logThrottle = Math.max(1, everyNFrames); }
    public void setSmoothing(float a){ this.ema = Math.max(0.05f, Math.min(0.9f, a)); }

    /** 返回 15 维 viseme 权重（0..1） */
    public float[] estimateVisemes(short[] pcm, int sampleRate) {
        if (pcm == null || pcm.length == 0 || sampleRate <= 0) return new float[15];

        // 1) FFT 长度（最近 2 次幂）
        int n=1; while(n<pcm.length) n<<=1; ensureFft(n);

        // 2) Hann 窗 & 归一化
        double inv32768 = 1.0/32768.0;
        for(int i=0;i<n;i++){
            double x = (i<pcm.length ? pcm[i]*inv32768 : 0.0);
            timeBuf[i] = x*window[i];
        }

        // 3) RMS（响度）
        double rms=0; for(short s: pcm){ double x=s*inv32768; rms+=x*x; }
        rms=Math.sqrt(rms/Math.max(1,pcm.length));
        float loud = mapf((float) rms, 0.01f, 0.25f);

        // 4) 频谱
        fft.fftReal(timeBuf, mag);
        int bins = n/2; double binHz = sampleRate/(double)n;

        int iLowEnd  = clampBin((int)Math.floor(300/binHz),  bins);
        int iMidEnd  = clampBin((int)Math.floor(2000/binHz), bins);
        int iHighEnd = clampBin((int)Math.floor(8000/binHz), bins);

        double eLow=0,eMid=0,eHigh=0,sum=0, cenNum=0;
        for(int i=1;i<=bins;i++){
            double a=mag[i], f=i*binHz;
            sum+=a; cenNum+=a*f;
            if(i<=iLowEnd) eLow+=a; else if(i<=iMidEnd) eMid+=a; else if(i<=iHighEnd) eHigh+=a;
        }
        double centroid = (sum>1e-9)?(cenNum/sum):0;
        float lowRatio  = (float) ((sum>1e-9)?(eLow/sum):0.0);
        float midRatio  = (float) ((sum>1e-9)?(eMid/sum):0.0);
        float highRatio = (float) ((sum>1e-9)?(eHigh/sum):0.0);

        // 5) ZCR / SFM / F1-F2 / 有声概率
        int zc=0; for (int i=1;i<pcm.length;i++){
            if ((pcm[i-1]>=0 && pcm[i]<0) || (pcm[i-1]<0 && pcm[i]>=0)) zc++;
        }
        float zcr = (float) zc / pcm.length;               // ~0.02-0.20
        float zcrNorm = clamp01f(mapf(zcr, 0.04f, 0.18f)); // 0→元音；1→擦音

        double arith=0, geom=0; int used=0;
        for (int i=1;i<=bins;i++){ double a=Math.max(mag[i],1e-8); arith+=a; geom+=Math.log(a); used++; }
        arith/=Math.max(1,used); geom=Math.exp(geom/Math.max(1,used));
        float sfm = clamp01f((float)(geom/arith));         // 越大越平坦→擦音感

        int f1Start = clampBin((int)Math.floor(300/binHz), bins);
        int f1End   = clampBin((int)Math.floor(900/binHz), bins);
        int f2Start = clampBin((int)Math.floor(900/binHz), bins);
        int f2End   = clampBin((int)Math.floor(2500/binHz), bins);
        double eF1=0, eF2=0;
        for (int i=f1Start;i<=f1End;i++) eF1 += mag[i];
        for (int i=f2Start;i<=f2End;i++) eF2 += mag[i];
        float rF1 = (float)(sum>1e-9 ? eF1/sum : 0f);
        float rF2 = (float)(sum>1e-9 ? eF2/sum : 0f);

        // 自相关做有声判定（80~300Hz）
        int minLag = Math.max(1, sampleRate / 300);
        int maxLag = Math.min(pcm.length - 1, Math.max(minLag + 1, sampleRate / 80));
        double r0 = 0.0;
        for (int i=0;i<pcm.length;i++){
            double x = pcm[i] * inv32768;
            r0 += x * x;
        }
        double maxCorr = 0.0;
        if (r0 > 1e-9) {
            for (int L = minLag; L <= maxLag; L++) {
                double sumCorr = 0.0;
                for (int i = L; i < pcm.length; i++) {
                    double x = pcm[i] * inv32768;
                    double y = pcm[i - L] * inv32768;
                    sumCorr += x * y;
                }
                double corr = sumCorr / (r0 + 1e-12);
                if (corr > maxCorr) maxCorr = corr;
            }
        }
        float voicedProb = clamp01f(mapf((float) maxCorr, 0.20f, 0.60f)); // 0→无声，1→强有声

        // 6) 几何中间参数
        float fricHint = clamp01f(0.6f*zcrNorm + 0.4f*sfm);
        fricEma += 0.30f * (fricHint - fricEma);
        float fricUsed = clamp01f(0.5f*fricHint + 0.5f*clamp01f(fricEma - 0.60f*voicedProb));

        float open   = clamp01f(0.55f*loud + 0.25f*normf((float)(eLow + eMid)) + 0.20f*(1f - zcrNorm));
        float round  = clamp01f(0.60f*lowRatio + 0.20f*mapf(2000f - (float)centroid, -1000f, 2000f) + 0.20f*(1f - zcrNorm));
        float width  = clamp01f(0.65f*midRatio + 0.35f*(1f - round));
        float lip    = clamp01f(0.5f*lowRatio + 0.5f*round);
        float teeth  = clamp01f( mapf(highRatio, 0.18f, 0.45f) * (0.20f + 0.80f*fricUsed) * (1f - 0.70f*voicedProb) );
        float tongue = clamp01f(0.6f*open + 0.4f*normf((float)eMid));

        // EMA 平滑
        smoothed[0]+=ema*(open   -smoothed[0]);
        smoothed[1]+=ema*(round  -smoothed[1]);
        smoothed[2]+=ema*(width  -smoothed[2]);
        smoothed[3]+=ema*(lip    -smoothed[3]);
        smoothed[4]+=ema*(teeth  -smoothed[4]);
        smoothed[5]+=ema*(tongue -smoothed[5]);

        // 7) 初步映射（带 F1/F2 强化 & 去 I 偏置）
        float[] v = new float[15];

        // 元音
        float aa = clamp01f((float)Math.pow(smoothed[0], 0.65f));
        float ii = clamp01f((float)Math.pow(smoothed[2], 0.75f));
        float oo = clamp01f((float)Math.pow(smoothed[1], 0.70f));
        float uu = clamp01f(0.6f*smoothed[1] + 0.4f*smoothed[3]);
        float ee = clamp01f(0.55f*smoothed[0] + 0.35f*smoothed[2]);

        // F1/F2 指示辅助
        aa *= (1.05f + 0.6f * clamp01f(mapf(rF1, 0.10f, 0.30f)));
        ii *= (1.02f + 0.7f * clamp01f(mapf(rF2, 0.10f, 0.30f)));
        oo *= (1.08f + 0.5f * clamp01f(mapf(lowRatio, 0.20f, 0.45f)));
        uu *= 1.15f;

        // —— 去 I 偏置：开口或圆唇显著时，削 I、补 AA/E/O/U —— //
        float iPenalty = clamp01f(0.45f*smoothed[0] + 0.45f*smoothed[1]); // open/round 越大，I 越该被削
        float iScaled  = ii * (1.0f - 0.60f * iPenalty);
        float iPool    = clamp01f(ii - iScaled);
        // 分配池
        float toAA = iPool * (0.50f * clamp01f(smoothed[0]));          // 开口大→AA
        float toOE = iPool - toAA;                                      // 其余给 O/U/E
        float toO  = toOE * 0.45f * clamp01f(smoothed[1]);              // 圆唇→O
        float toU  = toOE * 0.35f * clamp01f(smoothed[1]);
        float toE  = toOE - toO - toU;

        aa = clamp01f(aa + toAA);
        oo = clamp01f(oo + toO);
        uu = clamp01f(uu + toU);
        ee = clamp01f(ee + toE);
        ii = iScaled;

        v[8]  = aa;  // AA
        v[10] = ii;  // I
        v[11] = oo;  // O
        v[12] = uu;  // U
        v[9]  = ee;  // E

        // 辅音（擦音受 fricUsed 和有声抑制）
        float consGain = 0.40f + 0.90f * fricUsed;
        v[7]  = clamp01f((float)Math.pow(smoothed[4], 0.70f)) * consGain;             // SS
        v[2]  = clamp01f(smoothed[4] * 0.85f) * (0.85f * consGain);                   // FF
        v[6]  = clamp01f(0.65f*smoothed[1] + 0.20f*smoothed[2]) * (0.90f * consGain);// CH
        v[4]  = clamp01f(0.45f*smoothed[2] + 0.20f*smoothed[0]) * 0.85f;              // DD
        v[5]  = clamp01f(0.35f*smoothed[0] + 0.25f*smoothed[1]) * 0.85f;              // KK
        v[13] = clamp01f(0.55f*smoothed[1]) * 0.9f;                                   // RR

        // 静音/过渡
        float loudHint = mapf((float) rms, 0.00f, 0.10f);
        float sil = mapf(0.06f - loudHint*0.10f, 0.00f, 0.06f);
        v[0]  = 0.7f * sil;
        v[14] = 0.3f * sil;

        // —— Softmax（仅拉开，不做硬性最小占比） —— //
        final float T = 0.60f;
        float maxv = -1e9f; for (float x: v) if (x>maxv) maxv=x;
        float sumExp = 0f;
        for (int i=0;i<15;i++) { v[i] = (float) Math.exp((v[i] - maxv) / T); sumExp += v[i]; }
        for (int i=0;i<15;i++) v[i] = clamp01f(v[i] / Math.max(1e-6f, sumExp));

        // —— 温和 Hysteresis（优势不足时，向上一帧 Top 加一点偏置后再归一化） —— //
        int top = 0; float topVal = v[0];
        for (int i=1;i<15;i++) if (v[i] > topVal){ top=i; topVal=v[i]; }

        final float SWITCH_MARGIN = 0.10f; // 10% 优势才切换
        if (holdFrames > 0 && top != prevTop) {
            if (v[top] < v[prevTop] + SWITCH_MARGIN) {
                // 给上一帧 top 一个小偏置（不硬拉到某个常数）
                v[prevTop] = clamp01f(v[prevTop] + 0.08f);
                // 重新归一化
                float s=0f; for (float t: v) s+=t; if (s>1e-6f){ for (int i=0;i<15;i++) v[i]/=s; }
                top = prevTop;
                topVal = v[top];
            }
        }
        if (top == prevTop) { holdFrames++; } else { holdFrames=1; prevTop=top; }
        prevTopVal = topVal;

        // —— 日志 —— //
        if (logEnabled) {
            frameCount++;
            if (frameCount % logThrottle == 0) {
                Log.d(TAG, "===测试==== 输出嘴形数据===TopViseme: " + V_NAMES[top] + " ("+top+") val=" +
                        String.format("%.3f", topVal));
                Log.d(TAG, String.format(
                        "dbg zcr=%.3f sfm=%.3f low=%.3f mid=%.3f high=%.3f F1=%.3f F2=%.3f cent=%.0fHz",
                        zcr, sfm, lowRatio, midRatio, highRatio, rF1, rF2, centroid));
                Log.d(TAG, String.format(
                        "gate voiced=%.2f fricHint=%.2f fricEma=%.2f fricUsed=%.2f open=%.2f round=%.2f width=%.2f",
                        voicedProb, zcrNorm, fricEma, fricUsed, smoothed[0], smoothed[1], smoothed[2]));
            }
        }

        return v;
    }

    // —— helpers —— //
    private void ensureFft(int n){
        if (n==lastN) return;
        fft = new SimpleFFT(n);
        window = new double[n];
        timeBuf = new double[n];
        mag = new double[n/2 + 1];
        for(int i=0;i<n;i++) window[i] = 0.5*(1 - Math.cos(2*Math.PI*i/(n-1))); // Hann
        lastN = n;
        Arrays.fill(smoothed, 0f);
        fricEma = 0f;
        prevTop = 0; prevTopVal = 0f; holdFrames = 0;
    }

    private static int clampBin(int i,int bins){ return Math.max(1, Math.min(i,bins)); }
    private static float clamp01f(float v){ return v<0f?0f:(v>1f?1f:v); }
    private static float mapf(float v, float v0, float v1){
        if (v1 == v0) return 0f;
        float t = (v - v0) / (v1 - v0);
        return clamp01f(t);
    }
    private static float normf(float v){ return clamp01f(v / 10f); }
}
