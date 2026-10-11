package com.VideoProcess;

import android.content.Context;
import android.content.res.AssetManager;
import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.List;

public class ModelConfig {
    private final String TAG="ModelConfig";
    private String modelFilePath;




    public  ModelConfig(Context m_contex){

        try {
            modelFilePath = copyAssetsModelsToExternalStorage( m_contex);
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
    }

    public String get_Model_RootPath(){
            return  modelFilePath;
    }
    public String get_facedet_ModelPath( ) {

        String str = modelFilePath+"/faceDetModel.rknn";
        return str;
    }
    public String get_facefeat_ModelPath(){
        String str = modelFilePath+"/faceRecModel.rknn";
        return  str;
    }
    public String getIMAGE_PATH() {
        String IMAGE_PATH = modelFilePath + "/test.jpg";
        return IMAGE_PATH;
    }




    /**
     * A native method that is implemented by the 'faceRec' native library,
     * which is packaged with this application.
     */
    /**
     * 将 assets/models 目录下的所有文件复制到外部存储的 models 目录
     * @return 外部存储的 models 目录路径，失败返回 null
     */
    private String copyAssetsModelsToExternalStorage(Context m_contex) throws IOException {
        // 外部存储的 models 目录路径：/sdcard/Android/data/com.retinaface/files/models
        File externalModelsDir = new File(m_contex.getExternalFilesDir(null), "models");
        if (!externalModelsDir.exists()) {
            if (!externalModelsDir.mkdirs()) { // 创建目录
                Log.e(TAG, "无法创建外部存储目录");
                return null;
            }
        }

        // 检查是否已复制（通过标记文件判断）
        File flagFile = new File(externalModelsDir, ".copied");
        if (flagFile.exists()) {
          //  Log.d(TAG, "文件已复制，跳过");
            //return externalModelsDir.getAbsolutePath();
        }

        try {
            AssetManager assetManager = m_contex.getAssets();
            // 获取 assets/models 目录下的所有文件名
            String[] assetFiles = assetManager.list("models");
            if (assetFiles == null) {
                Log.e(TAG, "assets/models 目录不存在");
                return null;
            }

            // 逐个复制文件
            for (String fileName : assetFiles) {
                String assetPath = "models/" + fileName; // assets 中的源文件路径
                File destFile = new File(externalModelsDir, fileName); // 外部存储的目标文件

                // 复制文件（从 assets 到外部存储）
                try (InputStream in = assetManager.open(assetPath);
                     OutputStream out = new FileOutputStream(destFile)) {

                    byte[] buffer = new byte[1024 * 4]; // 4KB缓冲区
                    int bytesRead;
                    while ((bytesRead = in.read(buffer)) != -1) {
                        out.write(buffer, 0, bytesRead);
                    }
                    Log.d(TAG, "复制完成：" + fileName);
                } catch (IOException e) {
                    Log.e(TAG, "复制失败：" + fileName, e);
                    // 清除已复制的文件
                    //deleteFile(destFile);
                    return null;
                }
            }

            // 创建标记文件，避免下次重复复制
            flagFile.createNewFile();
            return externalModelsDir.getAbsolutePath();

        } catch (IOException e) {
            Log.e(TAG, "复制 assets 失败", e);
            return null;
        }
    }




}
