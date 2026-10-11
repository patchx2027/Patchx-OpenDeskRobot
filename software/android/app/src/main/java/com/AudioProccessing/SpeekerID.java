package com.AudioProccessing;

public class SpeekerID {

    static {
        System.loadLibrary("speekerid");
    }

    // 静态单例实例，饿汉式初始化
    private static final SpeekerID INSTANCE = new SpeekerID();

    // 私有构造函数，防止外部实例化
    private SpeekerID() {
        // 可以在这里进行必要的初始化操作
    }

    // 获取单例实例的静态方法
    public static SpeekerID getInstance() {
        return INSTANCE;
    }

    // 初始化声纹提取器（传入模型路径）
    public native boolean init(String onnxModelPath);

    // 释放资源
    public native void uninit();

    // 从PCM数据提取声纹特征
    // 参数：pcmData（16位单声道PCM数据）、sampleRate（采样率）、numSamples（采样点数）
    // 返回：声纹特征向量（float数组），失败返回null
    public native float[] extractAudioFeatureFromPCM(byte[] pcmData, int sampleRate, int numSamples);

    // 从音频文件提取声纹特征
    // 参数：filePath（音频文件路径，支持wav格式）
    // 返回：声纹特征向量（float数组），失败返回null
    public native float[] extractAudioFeatureFromFile(String filePath);



    // 新增：对比两个声纹特征向量的相似度
    // 参数：feature1、feature2 为提取到的声纹特征（float数组）
    // 返回：余弦相似度（0~1之间，值越大越相似）
    public native float compareFeatures(float[] feature1, float[] feature2);

}
