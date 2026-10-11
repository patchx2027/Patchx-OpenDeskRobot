package com.wcy.testserial;

import android.util.Log;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.util.Arrays;

public class FirmwareUpgradeManager {
    private static final String TAG = "FirmwareUpgrade";

    /** 每帧数据负载长度（256 字节） */
    private static final int FRAME_DATA_LEN = 256;

    /** 协议命令字 */
    private static final byte CMD_PREPARE = (byte) 0xDE; // 准备帧
    private static final byte CMD_DATA    = (byte) 0xDF; // 数据帧

    /** 最多重试次数 */
    private static final int MAX_RETRY = 3;

    private final SerialPortUtil serialPortUtil;

    private static final byte CMD_ANGLE = (byte) 0xFA;

    private static FirmwareUpgradeManager INSTANCE;

    public static FirmwareUpgradeManager i() {
        if (INSTANCE == null) {
            INSTANCE = new FirmwareUpgradeManager(null);
        }
        return INSTANCE;
    }


    public interface FirmwareUpgradeCallback {
        void onProgress(int current, int total);
        void onSuccess();
        void onFailure(String error);
    }

    public FirmwareUpgradeManager(SerialPortUtil serialPortUtil) {
        this.serialPortUtil = serialPortUtil;
    }

    public void sendFirmware(File binFile, FirmwareUpgradeCallback callback) {
        new Thread(() -> {
            try {
                if (serialPortUtil == null) {
                    callback.onFailure("串口未初始化");
                    return;
                }

                byte[] binData = readBinFile(binFile);
                int totalLen = binData.length;

                // 分包总数（每包 256）
                int totalPkgs = (int) Math.ceil(totalLen / (float) FRAME_DATA_LEN);
                Log.i(TAG, "开始发送升级包，总长度: " + totalLen + "，总包数: " + totalPkgs);

                // 1) 发送准备帧（DE + 总长度4字节；长度字段为 0x0005）
                if (!sendPrepareFrame(totalLen)) {
                    callback.onFailure("准备帧发送或应答失败");
                    return;
                }

                // 2) 从第 1 包开始发送（没有第 0 包）
                for (int pkgIndex = 1; pkgIndex <= totalPkgs; pkgIndex++) {
                    int offset = (pkgIndex - 1) * FRAME_DATA_LEN;
                    int len = Math.min(FRAME_DATA_LEN, totalLen - offset);
                    byte[] chunk = Arrays.copyOfRange(binData, offset, offset + len);

                    boolean success = false;
                    for (int retry = 0; retry < MAX_RETRY; retry++) {
                        byte[] dataFrame = buildDataFrame(pkgIndex, chunk);
                        Log.i(TAG, String.format("发送数据包#%d 帧(%d字节): %s",
                                pkgIndex, dataFrame.length, bytesToHex(dataFrame)));
                        serialPortUtil.write(dataFrame);

                        // 等设备 ACK
                        Thread.sleep(150);
                        byte[] resp = serialPortUtil.read();
                        Log.i(TAG, "数据帧应答: " + bytesToHex(resp));

                        // 规则：应答的第 5 字节（body[0]）应该回显我们发送的命令 DF
                        if (ackIsCommandEcho(resp, CMD_DATA)) {
                            success = true;
                            break;
                        } else {
                            Log.w(TAG, "未收到 DF 命令回显，重试 " + (retry + 1));
                        }
                    }

                    if (!success) {
                        callback.onFailure("数据包 #" + pkgIndex + " 发送失败");
                        return;
                    }

                    callback.onProgress(pkgIndex, totalPkgs);
                }

                callback.onSuccess();
                try { serialPortUtil.close(); } catch (Throwable ignore) {}

            } catch (Exception e) {
                Log.e(TAG, "升级异常: " + e.getMessage(), e);
                callback.onFailure("升级失败: " + e.getMessage());
            }
        }).start();
    }

    /** 发送准备帧：FF FF | 00 05 | DE + totalLen(4) | CRC_H CRC_L | 55 AA */
    private boolean sendPrepareFrame(int totalLen) throws InterruptedException {
        byte[] len4 = new byte[]{
                (byte) ((totalLen >> 24) & 0xFF),
                (byte) ((totalLen >> 16) & 0xFF),
                (byte) ((totalLen >> 8) & 0xFF),
                (byte) (totalLen & 0xFF)
        };
        byte[] body = new byte[1 + 4];
        body[0] = CMD_PREPARE;
        System.arraycopy(len4, 0, body, 1, 4);

        byte[] frame = wrapFrame(body);
        Log.i(TAG, "准备帧: " + bytesToHex(frame));
        serialPortUtil.write(frame);

        Thread.sleep(300);
        byte[] resp = serialPortUtil.read();
        Log.i(TAG, "准备帧应答: " + bytesToHex(resp));

        // 规则：应答的第 5 字节（body[0]）应该回显我们发送的命令 DE
        return ackIsCommandEcho(resp, CMD_PREPARE);
    }

    /** 数据帧：FF FF | LL LL | DF | pkgHi pkgLo | chunkLenHi chunkLenLo | chunk | CRC_H CRC_L | 55 AA */
    private byte[] buildDataFrame(int pkgIndex, byte[] chunk) {
        int chunkLen = (chunk != null) ? chunk.length : 0;

        // body = DF(1) + 包序号(2) + 片长(2) + 数据(N)
        byte[] body = new byte[1 + 2 + 2 + chunkLen];
        int p = 0;
        body[p++] = CMD_DATA;

        // 包序号：从 1 开始（高字节在前）
        body[p++] = (byte) ((pkgIndex >> 8) & 0xFF);
        body[p++] = (byte) (pkgIndex & 0xFF);

        // 本分包长度（高字节在前）
        body[p++] = (byte) ((chunkLen >> 8) & 0xFF);
        body[p++] = (byte) (chunkLen & 0xFF);

        if (chunkLen > 0) System.arraycopy(chunk, 0, body, p, chunkLen);

        return wrapFrame(body);
    }

    /**
     * 封装帧：
     * 头:  FF FF
     * 长度: 2字节（大端），值 = body.length
     * 数据: body
     * CRC: 2字节，高字节在前；CRC16(Modbus) 计算范围 = 从长度字段开始，共 (2 + bodyLen) 字节
     * 尾:  55 AA
     */
    private byte[] wrapFrame(byte[] body) {
        int len = body.length;
        byte[] frame = new byte[2 + 2 + len + 2 + 2];

        // 头
        frame[0] = (byte) 0xFF;
        frame[1] = (byte) 0xFF;

        // 长度（大端）
        frame[2] = (byte) ((len >> 8) & 0xFF);
        frame[3] = (byte) (len & 0xFF);

        // body
        System.arraycopy(body, 0, frame, 4, len);

        // CRC：从长度字段开始，长度为 2 + len
        int crc = crc16Modbus(frame, 2, 2 + len);

        // 高字节在前
        frame[4 + len] = (byte) ((crc >> 8) & 0xFF);
        frame[5 + len] = (byte) (crc & 0xFF);

        // 尾
        frame[6 + len] = (byte) 0x55;
        frame[7 + len] = (byte) 0xAA;

        // 调试：准备帧打印 CRC 输入与结果
        if (len == 5 && body[0] == CMD_PREPARE) {
            Log.i(TAG, "CRC输入(含长度): " + bytesToHex(Arrays.copyOfRange(frame, 2, 2 + 2 + len))
                    + " -> CRC=" + String.format("%02X %02X", ((crc >> 8) & 0xFF), (crc & 0xFF)));
        }
        return frame;
    }

    /** 判断应答是否为命令回显（第 5 字节 == 我们发的命令字） */
    private boolean ackIsCommandEcho(byte[] resp, byte expectCmd) {
        // 帧结构：FF FF | lenHi lenLo | body[0] body[1]...
        // 第 5 个字节 = body[0] = 命令
        return resp != null && resp.length >= 5 && resp[4] == expectCmd;
    }

    /** Modbus CRC16，多项式 0xA001，初值 0xFFFF；返回 0..0xFFFF */
    private int crc16Modbus(byte[] data, int offset, int len) {
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

    private byte[] readBinFile(File file) throws IOException {
        try (FileInputStream fis = new FileInputStream(file)) {
            byte[] data = new byte[(int) file.length()];
            int read = fis.read(data);
            if (read != data.length) throw new IOException("读取 bin 文件失败");
            return data;
        }
    }

    private String bytesToHex(byte[] bytes) {
        if (bytes == null) return "null";
        StringBuilder sb = new StringBuilder();
        for (byte b : bytes) sb.append(String.format("%02X ", b));
        return sb.toString().trim();
    }

    /**
     * 发送角度（0~65535；若你传入有负数，会按 short 的二进制补码发送）。
     * 返回 true 表示收到回显应答（body[0] == 0xFA）。
     */
    public boolean sendAngle(int angle) {
        // 规范到 16 位（支持负数->补码）
        int a = angle & 0xFFFF;

        // body = FA + angleHi + angleLo（大端）
        byte[] body = new byte[] {
                CMD_ANGLE,
                (byte) ((a >> 8) & 0xFF),
                (byte) (a & 0xFF)
        };

        // 封帧（沿用你现有的 wrapFrame/CRC 规则）
        byte[] frame = wrapFrame(body);
        Log.i(TAG, "角度帧: " + bytesToHex(frame));

        // 发送 + 等待应答（带重试）
        for (int retry = 0; retry < MAX_RETRY; retry++) {
            try {
                serialPortUtil.write(frame);
                Thread.sleep(150); // 视设备响应速度可调整
                byte[] resp = serialPortUtil.read();
                Log.i(TAG, "角度应答: " + bytesToHex(resp));
                if (ackIsCommandEcho(resp, CMD_ANGLE)) {
                    return true;
                } else {
                    Log.w(TAG, "角度命令未回显，重试 " + (retry + 1));
                }
            } catch (Exception e) {
                Log.w(TAG, "角度发送异常，重试 " + (retry + 1) + "，原因: " + e.getMessage());
            }
        }
        return false;
    }

    /**
     * 生成角度命令帧并返回十六进制字符串，不写串口
     * 格式：FF FF | lenHi lenLo | FA | angleHi angleLo | CRC_H CRC_L | 55 AA
     */
    public String buildAngleString(int angle) {
        int a = angle & 0xFFFF;
        byte[] body = new byte[]{
                CMD_ANGLE,
                (byte) ((a >> 8) & 0xFF),
                (byte) (a & 0xFF)
        };
        byte[] frame = wrapFrame(body);
        return bytesToHex(frame);
    }
}
