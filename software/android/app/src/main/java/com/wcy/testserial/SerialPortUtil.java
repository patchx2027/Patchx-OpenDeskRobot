package com.wcy.testserial;

import android.util.Log;

public class SerialPortUtil {

    private static final String TAG = "SerialPortUtil";

    static {
        System.loadLibrary("serial_port");
    }

    public native int open(String path, int baudrate);
    public native void close();
    public native int write(byte[] data);
    public native byte[] read();

    /**
     * 封装写入并读取应答（可选）
     */
    public byte[] writeAndRead(String msg) {
        int result = write(msg.getBytes());
        Log.i(TAG, "Write Result: " + result);
        try {
            Thread.sleep(100); // 适当等待串口响应
        } catch (InterruptedException e) {
            e.printStackTrace();
        }
        byte[] response = read();
        if (response != null) {
            Log.i(TAG, "Read bytes: " + response.length);
        } else {
            Log.i(TAG, "No response received");
        }
        return response;
    }
}
