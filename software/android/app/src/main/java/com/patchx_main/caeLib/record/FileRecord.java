package com.patchx_main.caeLib.record;

import android.content.Context;
import android.util.Log;

import java.io.DataInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 从文件读取pcm 数据
 */
public class FileRecord extends BaseRecord {
    private String mPath;
    private ExecutorService executors;
    private volatile boolean isStop = false;

    public FileRecord(String mPath, int sample, int channel) {
        this.mPath = mPath;
        this.sample = sample;
        this.channel = channel;
    }

    @Override
    public boolean isNeedCheck() {
        return false;
    }

    @Override
    public void init(Context context) {
        //runnable -> new Thread("FileRecordThread")
        executors = Executors.newSingleThreadExecutor();
    }

    @Override
    public boolean startRecord() {
        File file = new File(mPath);
        if (!file.exists()) {
            return false;
        }
        startReadData();
        return true;
    }

    private static final String TAG = "FileRecord";

    private void startReadData() {
        executors.execute(() -> {
            byte[] audioData = new byte[1024 * channel];
            FileInputStream out = null;
            try {
                Thread.sleep(4000);
                out = new FileInputStream(mPath);
                DataInputStream dataOutputStream = new DataInputStream(out);
                while (!isStop) {
                    int available = dataOutputStream.available();
                    if (available >= audioData.length) {
                        Log.e(TAG, "startReadData: " + available);
                        dataOutputStream.readFully(audioData);
                        onAudioCallBack(audioData, audioData.length);
                        Thread.sleep(10);
                    } else {
                        isStop = true;
                    }
                }
            } catch (FileNotFoundException e) {
                e.printStackTrace();
            } catch (IOException e) {
                e.printStackTrace();
            } catch (InterruptedException e) {
                e.printStackTrace();
            } finally {
                if (out != null) {
                    try {
                        out.close();
                    } catch (IOException e) {
                        e.printStackTrace();
                    }
                }
            }
        });
    }

    @Override
    public void stopRecord() {
        isStop = true;
    }
}
