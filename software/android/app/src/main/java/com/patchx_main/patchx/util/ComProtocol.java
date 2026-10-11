package com.patchx_main.patchx.util;

import android.util.Log;
import com.wcy.testserial.SerialPortUtil;

/**
 * 帧：FF FF | lenHi lenLo | CMD | DATA... | CRC_H CRC_L | 55 AA
 * len = 1(命令) + dataLen
 * CRC16(Modbus, poly 0xA001, init 0xFFFF)，计算区间 = 从长度字段开始，共(2 + len)字节；CRC 高字节在前。
 */
public final class ComProtocol {
    private static final String TAG = "ComProtocol";

    public static final byte HEAD_H = (byte) 0xFF;
    public static final byte HEAD_L = (byte) 0xFF;
    public static final byte TAIL_H = (byte) 0x55;
    public static final byte TAIL_L = (byte) 0xAA;

    private ComProtocol() {}

    /** 发送：等价于 C 的 COM_TransferDatas */
    public static boolean COM_TransferDatas(SerialPortUtil serial, int cmd, byte[] data, int dataLen) {
        if (serial == null) {
            Log.e(TAG, "SerialPortUtil is null");
            return false;
        }
        byte[] frame = buildFrame(cmd, data, dataLen);
        if (frame == null) return false;
        try {
            Log.i(TAG, "测试===动作系统下发====COM_SendBuf = " + bytesToHex(frame));
            serial.write(frame);
            return true;
        } catch (Exception e) {
            Log.e(TAG, "COM_TransferDatas send error", e);
            return false;
        }
    }

    /** 构建无数据区的“单字节命令”帧：len 固定为 0x0001 */
    public static byte[] buildSimpleFrame(int cmd) {
        return buildFrame(cmd, null, 0);
    }

    /** 构建完整帧（不发送） */
    public static byte[] buildFrame(int cmd, byte[] data, int dataLen) {
        if (data == null) data = new byte[0];
        if (dataLen < 0 || dataLen > data.length) dataLen = data.length;

        int len = 1 + dataLen;                 // body 长度 = 命令(1) + 数据
        int frameLen = 2 + 2 + len + 2 + 2;    // 头2 + 长2 + body + CRC2 + 尾2
        byte[] frame = new byte[frameLen];
        int p = 0;

        // 头
        frame[p++] = HEAD_H;
        frame[p++] = HEAD_L;

        // 长度（大端）
        frame[p++] = (byte) ((len >> 8) & 0xFF);
        frame[p++] = (byte) (len & 0xFF);

        // 命令
        frame[p++] = (byte) (cmd & 0xFF);

        // 数据
        if (dataLen > 0) {
            System.arraycopy(data, 0, frame, p, dataLen);
            p += dataLen;
        }

        // 计算 CRC：从长度字段开始（frame[2]），长度 = 2 + len
        int crcStart = 2;
        int crcLen = 2 + len;
        int crc = crc16Modbus(frame, crcStart, crcLen);

        // CRC 高字节在前
        frame[p++] = (byte) ((crc >> 8) & 0xFF);
        frame[p++] = (byte) (crc & 0xFF);

        // 尾
        frame[p++] = TAIL_H;
        frame[p++] = TAIL_L;

        return frame;
    }

    /** 判断应答是否为“命令回显”：第 5 字节 frame[4] == CMD */
    public static boolean isAckCmdEcho(byte[] resp, byte expectCmd) {
        return resp != null && resp.length >= 5 && resp[4] == expectCmd;
    }

    /** Modbus CRC16：poly 0xA001, init 0xFFFF；返回 0..0xFFFF */
    public static int crc16Modbus(byte[] data, int offset, int len) {
        int crc = 0xFFFF;
        int end = offset + len;
        for (int i = offset; i < end; i++) {
            crc ^= (data[i] & 0xFF);
            for (int j = 0; j < 8; j++) {
                if ((crc & 1) != 0) crc = (crc >> 1) ^ 0xA001;
                else crc >>= 1;
            }
        }
        return crc & 0xFFFF;
    }

    /** 十六进制打印 */
    public static String bytesToHex(byte[] bytes) {
        if (bytes == null) return "null";
        StringBuilder sb = new StringBuilder();
        for (byte b : bytes) sb.append(String.format("%02X ", b));
        return sb.toString().trim();
    }

    private static void checkOne(int cmd, String expectedHex) {
        byte[] frame = buildSimpleFrame(cmd);
        String hex = bytesToHex(frame);
        boolean ok = expectedHex.equalsIgnoreCase(hex);
        Log.i(TAG, "CMD=" + cmd + " -> " + hex + "  " + (ok ? "OK" : "MISMATCH (expect " + expectedHex + ")"));
    }

    public static String getFrameHexForCmd(int cmd) {
        return bytesToHex(buildSimpleFrame(cmd));
    }
}
