package com.patchx_main.patchx.manage;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Environment;
import android.util.Log;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

public class ImageSaver {
    private Context context;
    public ImageSaver(Context contxt){
        context=contxt;
    }

    // 保存 JPEG 字节数组到应用私有目录
    public  String saveJpgToPrivateDir(byte[] jpgbuf, String file_head) {

        // 生成带时间戳的文件名
        SimpleDateFormat sdf = new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.getDefault());
        String timeStamp = sdf.format(new Date());

        String fileName = file_head + timeStamp;


        if (jpgbuf == null || jpgbuf.length == 0) {
            Log.d("imgsver", "error + jpgbuf null");
            return null; // 字节数组为空，保存失败
        }

        // 1. 获取应用私有存储目录（/data/data/[包名]/files/）
        File filesDir = context.getExternalFilesDir(null);
        // 可选：创建子目录（如 images/），避免文件混乱
        File imageDir = new File(filesDir, "images");
        if (!imageDir.exists()) {
            imageDir.mkdirs(); // 目录不存在则创建
        }

        // 2. 定义保存的文件路径（完整路径：/data/data/[包名]/files/images/xxx.jpg）
        File imageFile = new File(imageDir, fileName + ".jpg");
        Log.d("imgsaver",imageFile.getAbsolutePath());
        try (FileOutputStream fos = new FileOutputStream(imageFile)) {
            fos.write(jpgbuf); // 直接写入原始JPEG数据
            fos.flush();
            return imageFile.getAbsolutePath();
        } catch (IOException e) {
            e.printStackTrace();
            return null;
        }
    }
}
