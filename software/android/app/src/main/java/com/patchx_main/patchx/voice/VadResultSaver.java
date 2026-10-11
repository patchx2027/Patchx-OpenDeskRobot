package com.patchx_main.patchx.voice;

import android.content.Context;
import android.os.Environment;
import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

public class VadResultSaver {
    private static final String TAG = "VadResultSaver";
    private static final String VAD_FOLDER = "vad_results";
    // 根据实际情况修改音频参数
    private static final int SAMPLE_RATE = 16000; // 采样率，根据实际情况调整
    private static final int BITS_PER_SAMPLE = 16; // 位深度
    private static final int CHANNELS = 1; // 声道数，单声道

    private final Context mContext;

    public VadResultSaver(Context context) {
        this.mContext = context;
        // 获取外部存储的应用专用目录
        File externalFilesDir = mContext.getExternalFilesDir(null);
        if (externalFilesDir == null) {
            Log.e(TAG, "外部存储不可用");
            return ;
        }
        deleteDir(new File(externalFilesDir, VAD_FOLDER));
    }

    /**
     * 将short数组保存为WAV文件
     * @param vadResult 音频数据
     * @return 保存的文件路径，失败返回null
     */
    public String saveVadResultToWav(short[] vadResult) {
        // 创建保存目录
        File saveDir = getSaveDirectory();
        if (saveDir == null) {
            Log.e(TAG, "无法创建保存目录");
            return null;
        }

        // 生成时间戳文件名
        String fileName = generateFileName();
        File wavFile = new File(saveDir, fileName);

        try {
            // 保存为WAV文件
            boolean success = writeWavFile(wavFile, vadResult);
            if (success) {
                Log.d(TAG, "WAV文件保存成功: " + wavFile.getAbsolutePath());
                return wavFile.getAbsolutePath();
            } else {
                Log.e(TAG, "WAV文件保存失败");
                return null;
            }
        } catch (IOException e) {
            Log.e(TAG, "保存文件时发生错误: " + e.getMessage());
            e.printStackTrace();
            return null;
        }
    }

    /**
     * 获取保存目录
     * 路径: /sdcard/Android/data/包名/files/vad_results/
     */
    private File getSaveDirectory() {
        // 获取外部存储的应用专用目录
        File externalFilesDir = mContext.getExternalFilesDir(null);
        if (externalFilesDir == null) {
            Log.e(TAG, "外部存储不可用");
            return null;
        }

        // 创建VAD专用文件夹
        File vadDir = new File(externalFilesDir, VAD_FOLDER);
        if (!vadDir.exists()) {
            boolean isCreated = vadDir.mkdirs();
            if (!isCreated) {
                Log.e(TAG, "无法创建VAD结果文件夹");
                return null;
            }
        }

        return vadDir;
    }

    /**
     * 生成时间戳文件名，格式: yyyyMMdd_HHmmss_SSS.wav
     */
    private String generateFileName() {
        SimpleDateFormat sdf = new SimpleDateFormat("yyyyMMdd_HHmmss_SSS", Locale.getDefault());
        return sdf.format(new Date()) + ".wav";
    }

    /**
     * 将short数组写入WAV文件
     */
    private boolean writeWavFile(File file, short[] data) throws IOException {
        if (data == null || data.length == 0) {
            Log.e(TAG, "音频数据为空");
            return false;
        }

        FileOutputStream outputStream = new FileOutputStream(file);

        // 计算数据大小
        int dataSize = data.length * 2; // 每个short占2字节
        int fileSize = 36 + dataSize; // WAV文件头36字节 + 数据大小

        // 写入WAV文件头
        writeString(outputStream, "RIFF"); // Chunk ID
        writeInt(outputStream, fileSize); // Chunk Size
        writeString(outputStream, "WAVE"); // Format
        writeString(outputStream, "fmt "); // Subchunk 1 ID
        writeInt(outputStream, 16); // Subchunk 1 Size (PCM)
        writeShort(outputStream, (short) 1); // Audio Format (PCM = 1)
        writeShort(outputStream, (short) CHANNELS); // Num Channels
        writeInt(outputStream, SAMPLE_RATE); // Sample Rate
        writeInt(outputStream, SAMPLE_RATE * CHANNELS * BITS_PER_SAMPLE / 8); // Byte Rate
        writeShort(outputStream, (short) (CHANNELS * BITS_PER_SAMPLE / 8)); // Block Align
        writeShort(outputStream, (short) BITS_PER_SAMPLE); // Bits Per Sample
        writeString(outputStream, "data"); // Subchunk 2 ID
        writeInt(outputStream, dataSize); // Subchunk 2 Size

        // 写入音频数据
        byte[] bytes = shortToByteArray(data);
        outputStream.write(bytes);

        outputStream.close();
        return true;
    }

    /**
     * 将short数组转换为byte数组
     */
    private byte[] shortToByteArray(short[] data) {
        int len = data.length;
        byte[] bytes = new byte[len * 2];
        for (int i = 0; i < len; i++) {
            bytes[2 * i] = (byte) (data[i] & 0x00FF);
            bytes[2 * i + 1] = (byte) ((data[i] & 0xFF00) >> 8);
        }
        return bytes;
    }

    // 辅助方法：写入字符串
    private void writeString(FileOutputStream out, String s) throws IOException {
        out.write(s.getBytes());
    }

    // 辅助方法：写入int
    private void writeInt(FileOutputStream out, int i) throws IOException {
        out.write(i >> 0);
        out.write(i >> 8);
        out.write(i >> 16);
        out.write(i >> 24);
    }

    // 辅助方法：写入short
    private void writeShort(FileOutputStream out, short s) throws IOException {
        out.write(s >> 0);
        out.write(s >> 8);
    }



    /**
     * 递归删除目录（包括子文件和子目录）
     */
    private static boolean deleteDir(File dir) {
        if (dir == null || !dir.exists()) {
            return true;
        }

        // 先删除所有子文件和子目录
        File[] files = dir.listFiles();
        if (files != null) {
            for (File file : files) {
                if (file.isDirectory()) {
                    // 递归删除子目录
                    if (!deleteDir(file)) {
                        return false;
                    }
                } else {
                    // 删除文件
                    if (!file.delete()) {
                        return false;
                    }
                }
            }
        }
        Log.d("VAD_DIR","cleaning");
        // 最后删除当前空目录
        return dir.delete();
    }



}
