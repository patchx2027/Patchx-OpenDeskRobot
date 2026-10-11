package com.VideoProcess;
import java.util.List;

public class ImageProcessing {
    // 加载JNI库
    static {
        System.loadLibrary("video_process");
    }
    private static final ImageProcessing _instance=new ImageProcessing();

    private ImageProcessing() {

    }
    public static  ImageProcessing getInstance(){
        return _instance;
    }

  //全局 初始化 ，设置内部的cnn权重文件目录
    public native boolean init(String ModelPath);
    public native void uninit();


    //图像识别分类,返回json数据。json为图像分类标签和概率
    public native String ImageRecog();


   //获取人脸识别信息 ，返回的是人脸id 和人脸相对于图像中心的位置，如果是-1则无人脸信息
    public native String GetFaceInfo();

   //相机相关，开相机后才能进行物体识别和人脸识别
    public native boolean openCamera(int cam_id);
    public native boolean closeCamera();
    public native  byte[] takePicture(String img_path);

    public native float[] extractFaceFeatureJPG(String jpg_path);

}
