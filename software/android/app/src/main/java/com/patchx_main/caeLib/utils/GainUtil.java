package com.patchx_main.caeLib.utils;

import android.util.Log;

public class GainUtil {
    private static final String TAG = "GainUtil";

    private static short GainUp(short data, float fv) {
        float pcmval = data * fv;
        return (short) (pcmval);
    }

    /**
     * CAE降噪后的音频为小端对齐格式，低字节在低位，增益调整 aplication 层
     *
     * @param buffer
     * @return
     */
    public static byte[] audioDataHandle(byte[] buffer, float gainValue) {
        if (null == buffer) {
            return null;
        }
        if (gainValue == 0 || gainValue == 1) {
            return buffer;
        }
        int cnt = 0;
        short temp = 0;
        byte[] bufferT = new byte[buffer.length];
        while (cnt < buffer.length) {
            temp = (short) (buffer[cnt + 1]);//获取short 高位
            temp = (short) ((temp << 8) | (buffer[cnt] & 0xff));//获取低位
            temp = GainUp(temp, gainValue);//做音量调节处理
            if (temp > 32767) {
                temp = 32767;
            } else if (temp < -32768) {
                temp = -32768;
            }
            //拆分为byte后再写到文件中
            bufferT[cnt + 1] = (byte) ((temp >> 8) & 0xff); //高位
            bufferT[cnt] = (byte) (temp & 0xff);//低位
            cnt += 2;
        }
        System.gc();
        return bufferT;
    }
}
