package com.patchx_main.caeLib.dataAdapter;

public class BothlentAdapter extends BaseDataAdapter {
    @Override
    public byte[] adapter(byte[] source) {
        return adapter4Mic(source);
    }

    // 16k，16bit，8通道 -->  16k,32bit,6通道
    private static byte[] adapter4Mic(byte[] source) {
        int size = (source.length / 8) * 6 * 2;
        if (outData == null || outData.length != size) {
            outData = new byte[size];
        }
        int j = 0;
        while (j < source.length / 16) {
            outData[24 * j + 0] = 0x00;
            outData[24 * j + 1] = 0x01;
            outData[24 * j + 2] = source[16 * j + 0];
            outData[24 * j + 3] = source[16 * j + 1];

            outData[24 * j + 4] = 0x00;
            outData[24 * j + 5] = 0x02;
            outData[24 * j + 6] = source[16 * j + 2];
            outData[24 * j + 7] = source[16 * j + 3];

            outData[24 * j + 8] = 0x00;
            outData[24 * j + 9] = 0x03;
            outData[24 * j + 10] = source[16 * j + 4];
            outData[24 * j + 11] = source[16 * j + 5];

            outData[24 * j + 12] = 0x00;
            outData[24 * j + 13] = 0x04;
            outData[24 * j + 14] = source[16 * j + 6];
            outData[24 * j + 15] = source[16 * j + 7];

            //通道7--》ref1
            outData[24 * j + 16] = 0x00;
            outData[24 * j + 17] = 0x05;
            outData[24 * j + 18] = source[16 * j + 12];
            outData[24 * j + 19] = source[16 * j + 13];

            //通道8 --》 ref2
            outData[24 * j + 20] = 0x00;
            outData[24 * j + 21] = 0x06;
            outData[24 * j + 22] = source[16 * j + 14];
            outData[24 * j + 23] = source[16 * j + 15];

            j++;
        }
        return outData;
    }


    // 16k，16bit，8通道 -->  16k,32bit,8通道
    public static byte[] adapter6Mic(byte[] data) {
        int size = data.length * 2;
        if (outData == null || outData.length != size) {
            outData = new byte[size];
        }
        int j = 0;
        int k = 0;
        int index = 0;
        int step = data.length / 2;

        while (j < step) {// 除以2是两个字节作为一组数据，进行添加通道号处理；
            for (int i = 1; i < 9; i++) {
                k = 4 * j;
                index = 2 * j;
                outData[k] = 00;
                outData[k + 1] = (byte) i;
                outData[k + 2] = data[index];
                outData[k + 3] = data[index + 1];
                j++;
            }

        }
        return outData;
    }
}
