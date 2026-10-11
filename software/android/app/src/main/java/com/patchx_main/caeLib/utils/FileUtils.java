package com.patchx_main.caeLib.utils;

import android.content.Context;
import android.text.TextUtils;
import android.util.Log;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class FileUtils {
    /**
     * @param context
     * @param assetDir
     * @param assetName
     * @param parentPath
     * @throws IOException
     */
    public static void copyAssets2Sdcard(Context context, String assetDir, String assetName, String parentPath) throws IOException {
        Log.e(TAG, "copyAssets2Sdcard: " + assetDir + ";  assetName=" + assetName + "  parentPath=" + parentPath);
        File parentDir = new File(parentPath);
        if (!parentDir.exists()) {
            parentDir.mkdirs();
        }
        InputStream fileDescriptor = null;
        FileOutputStream fos = null;
        try {
            fileDescriptor = context.getAssets().open((assetDir + assetName).replace(" ","".trim()));
            File sdFile = new File(parentDir, assetName);
            if (sdFile.exists()) {
                sdFile.delete();
            }
            sdFile.createNewFile();

            fos = new FileOutputStream(sdFile);
            byte[] buffer = new byte[1024];
            int byteCount = 0;
            while ((byteCount = fileDescriptor.read(buffer)) != -1) {// 循环从输入流读取
                // buffer字节
                fos.write(buffer, 0, byteCount);// 将读取的输入流写入到输出流
            }
            fos.flush();// 刷新缓冲区

        } catch (IOException e) {
            e.printStackTrace();
            Log.e(TAG, "copyAssets2Sdcard:" + e + ";");
        } finally {
            if (fileDescriptor != null) {
                fileDescriptor.close();
            }
            if (fos != null) {
                fos.close();
            }
        }

    }

    public static boolean exists(String path) {
        return new File(path).exists();
    }

    private static final String TAG = "FileUtils";

    public static Map<String, String> readAssetValueByKey(Context context, String name, List<String> list) {
        HashMap<String, String> stringStringHashMap = new HashMap<>();
        BufferedReader inputStream = null;
        try {
            inputStream = new BufferedReader(new InputStreamReader(context.getAssets().open(name)));
            while (inputStream.ready()) {
                String s = inputStream.readLine();
                if (!TextUtils.isEmpty(s)) {
                    String[] split = s.split("=");
                    if (split.length == 2) {
                        String key = split[0].replace(" ", "");
                        String value = split[1].replace(" ", "").split(";")[0];
                        if (list.contains(key)) {
                            String replace = value.trim().replace(" ", "");
                            stringStringHashMap.put(key, replace);
                        }
                    }
                }
            }
            return stringStringHashMap;
        } catch (IOException e) {
            e.printStackTrace();
            return null;
        } finally {
            if (inputStream != null) {
                try {
                    inputStream.close();
                } catch (IOException e) {
                    e.printStackTrace();
                }
            }
        }
    }


    public static String readFile(String path) {
        String content = "";
        InputStream fileOutputStream = null;
        try {
            fileOutputStream = new FileInputStream(path);
            byte[] bytes = new byte[fileOutputStream.available()];
            int read = fileOutputStream.read(bytes);
            content = new String(bytes, 0, read);
        } catch (IOException io) {
            io.printStackTrace();
        } finally {
            if (fileOutputStream != null) {
                try {
                    fileOutputStream.close();
                    fileOutputStream = null;
                } catch (IOException e) {

                }
            }
        }
        return content;
    }

    public static String getAssetsContent(Context context, String path) {
        int len = 0;
        byte[] buf = null;
        String result = "";
        try {
            InputStream in = context.getAssets().open(path);
            len = in.available();
            buf = new byte[len];
            in.read(buf, 0, len);

            result = new String(buf);
        } catch (Exception e) {
            e.printStackTrace();
        }
        return result;
    }

}
