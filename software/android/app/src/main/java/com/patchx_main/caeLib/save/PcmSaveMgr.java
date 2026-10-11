package com.patchx_main.caeLib.save;

import android.text.TextUtils;
import android.util.Log;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class PcmSaveMgr {
    private static final String TAG = "PcmSaveMgr";
    private static PcmSaveMgr instance = new PcmSaveMgr();
    private static String pcmParentDir;
    private FileChannel fileChannelRaw;
    private FileChannel fileChannelBefore;
    private FileChannel fileChannelAfter;
    private final String rawDir = "PcmAudio";
    private final String beforeDir = "CaeRawAudio";
    private final String afterDir = "CaeAsrAudio";

    private PcmSaveMgr() {
    }

    public static PcmSaveMgr getInstance() {
        return instance;
    }

    public void init(String path) {
        pcmParentDir = path;
    }

    /**
     * 通过开关控制，这个地方可能会被 多线程访问
     */
    private volatile boolean isSavePcm;

    public boolean isSavePcm() {
        return isSavePcm;
    }

    public void setSavePcm(boolean savePcm) throws FileNotFoundException {
        if (isSavePcm == savePcm) {
            return;
        }
        if (savePcm) { //开始保存
            if (TextUtils.isEmpty(pcmParentDir)) {
                throw new UnsupportedOperationException("must invoke init() before");
            }
            startSave();
        } else { //停止保存
            stopSave();
        }
        isSavePcm = savePcm;
    }

    private void stopSave() {
        if (fileChannelRaw != null) {
            try {
                fileChannelRaw.close();
            } catch (IOException e) {
                e.printStackTrace();
            } finally {
                fileChannelRaw = null;
            }
        }
        if (fileChannelBefore != null) {
            try {
                fileChannelBefore.close();
            } catch (IOException e) {
                e.printStackTrace();
            } finally {
                fileChannelBefore = null;
            }
        }
        if (fileChannelAfter != null) {
            try {
                fileChannelAfter.close();
            } catch (IOException e) {
                e.printStackTrace();
            } finally {
                fileChannelAfter = null;
            }
        }
    }

    private void startSave() {
        SimpleDateFormat simpleDateFormat = new SimpleDateFormat("yyyy-MM-dd_hh_mm_ss", Locale.getDefault());
        String fileName = simpleDateFormat.format(new Date());
        fileChannelRaw = openFile(pcmParentDir, rawDir, fileName).getChannel();
        fileChannelBefore = openFile(pcmParentDir, beforeDir, fileName).getChannel();
        fileChannelAfter = openFile(pcmParentDir, afterDir, fileName).getChannel();

    }

    private FileOutputStream openFile(String pcmParentDir, String rawDir, String fileName) {
        File parentDir = new File(pcmParentDir, rawDir);
        if (!parentDir.exists()) {
            boolean mkdirs = parentDir.mkdirs();
            if (!mkdirs) {
                Log.e(TAG, "openFile: mkdir exceptions " + parentDir.getAbsolutePath());
                return null;
            }
        }
        String path = String.format("%s/%s/%s.pcm", pcmParentDir, rawDir, fileName);
        Log.e(TAG, "openFile: " + path);
        File file = new File(path);
        if (file.exists()) {
            boolean delete = file.delete();
            if (!delete) {
                return null;
            }
        }
        try {
            boolean newFile = file.createNewFile();
            if (newFile) {
                return new FileOutputStream(file);
            }
        } catch (IOException e) {
            e.printStackTrace();
        }
        return null;
    }

    public void writeRawPcm(byte[] data, int len) {
        if (isSavePcm) {
            try {
                fileChannelRaw.write(ByteBuffer.wrap(data,0,len));
            } catch (IOException e) {
                e.printStackTrace();
            }
        }
    }

    public void writeBeforePcm(byte[] data, int len) {
        if (isSavePcm) {
            try {
                fileChannelBefore.write(ByteBuffer.wrap(data,0,len));
            } catch (IOException e) {
                e.printStackTrace();
            }
        }
    }

    public void writeAfterPcm(byte[] data, int len) {
        if (isSavePcm) {
            try {
                fileChannelAfter.write(ByteBuffer.wrap(data,0,len));
            } catch (IOException e) {
                e.printStackTrace();
            }
        }
    }
}
