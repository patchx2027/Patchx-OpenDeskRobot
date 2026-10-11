package com.patchx_main.patchx.voice;


import java.nio.ByteBuffer;
import java.nio.ByteOrder;

public class AudioUtils {

    /** 将整个字节数组转换为 short 数组（16位PCM） */
    public static short[] bytesToShorts(byte[] bytes) {
        short[] shorts = new short[bytes.length / 2];
        ByteBuffer.wrap(bytes)
                .order(ByteOrder.LITTLE_ENDIAN)
                .asShortBuffer()
                .get(shorts);
        return shorts;
    }

    /** 将字节数组的前 length 字节转换为 short 数组（16位PCM） */
    public static short[] bytesToShorts(byte[] bytes, int length) {
        if (length % 2 != 0) length -= 1;
        short[] shorts = new short[length / 2];
        ByteBuffer.wrap(bytes, 0, length)
                .order(ByteOrder.LITTLE_ENDIAN)
                .asShortBuffer()
                .get(shorts);
        return shorts;
    }

    /** 将 short 数组转换为字节数组（16位PCM） */
    public static byte[] shortsToBytes(short[] shorts) {
        ByteBuffer buffer = ByteBuffer.allocate(shorts.length * 2);
        buffer.order(ByteOrder.LITTLE_ENDIAN);
        buffer.asShortBuffer().put(shorts);
        return buffer.array();
    }

    /** 将 short 数组的一部分转换为字节数组 */
    public static byte[] shortsToBytes(short[] shorts, int length) {
        ByteBuffer buffer = ByteBuffer.allocate(length * 2);
        buffer.order(ByteOrder.LITTLE_ENDIAN);
        buffer.asShortBuffer().put(shorts, 0, length);
        return buffer.array();
    }
}
