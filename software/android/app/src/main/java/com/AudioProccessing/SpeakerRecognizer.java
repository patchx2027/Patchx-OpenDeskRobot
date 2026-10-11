package com.AudioProccessing;

import android.os.Environment;
import android.util.Log;

import java.io.File;
import java.util.HashMap;
import java.util.Map;

public class SpeakerRecognizer {


    private  static  final String TAG="SpeakerRecognizer";
    // 存储用户ID与声纹特征的映射
    private final Map<String, float[]> userFeatures = new HashMap<>();
    // 声纹处理实例
    private final SpeekerID speekerID;
    // 相似度阈值，用于判断是否匹配成功
    //private static final float SIMILARITY_THRESHOLD = 0.7f;

    private  static final SpeakerRecognizer _instance = new SpeakerRecognizer();

    public  static SpeakerRecognizer  getInstance(){
        return _instance;
    }
    public SpeakerRecognizer() {
        speekerID = SpeekerID.getInstance();
    }

    /**
     * 初始化：加载模型并预处理已知用户的音频文件
     * @param modelPath 模型文件路径
     * @return 初始化是否成功
     */
    public boolean init(String modelPath) {
        // 初始化声纹提取器
        boolean initSuccess = speekerID.init(modelPath);
        if (!initSuccess) {
            Log.d(TAG," speekerID.init error !!!!!!!!!");
            return false;
        }

        // 加载已知用户的音频文件并提取特征
        loadKnownUserFeatures();
        if(1==1) {
           float[] f1 =  userFeatures.get("1");
           float[] f2 =  userFeatures.get("2");
           float sim = speekerID.compareFeatures(f1,f2);
           Log.d(TAG,"sim:"+sim);
        }
        Log.d(TAG,"audio_features:"+userFeatures.size());
        return true;
    }

    /**
     * 加载已知用户的音频文件并提取特征
     */
    private void loadKnownUserFeatures() {
        // 获取存储已知音频文件的目录
        String audioDirPath ="/mnt/sdcard/Download/WavDatabase";
        File audioDir = new File(audioDirPath);

        if (audioDir.exists() && audioDir.isDirectory()) {
            File[] audioFiles = audioDir.listFiles((dir, name) ->
                    name.endsWith(".wav"));

            if (audioFiles != null) {
                for (File file : audioFiles) {
                    // 从文件名提取用户ID（假设文件名格式为 "id_xxx.wav"）
                    String fileName = file.getName();
                    String userId = extractUserIdFromFileName(fileName);
                    Log.d(TAG,fileName);

                    if (userId != null) {
                        // 提取声纹特征
                        float[] feature = speekerID.extractAudioFeatureFromFile(file.getAbsolutePath());
                        if (feature != null) {
                            userFeatures.put(userId, feature);
                        }
                    }
                }
            }
        }
    }

    /**
     * 从文件名提取用户ID（根据实际命名规则修改）
     */
    private String extractUserIdFromFileName(String fileName) {
        try {
            // 假设文件名格式为 "1_0.wav"，提取第一个下划线前的部分作为ID
            return fileName.split("_")[0];
        } catch (Exception e) {
            e.printStackTrace();
            return null;
        }
    }

    /**
     * 识别实时语音片段属于哪个用户
     * @param pcmData 实时语音的PCM数据（16位单声道）
     * @param sampleRate 采样率
     * @param numSamples 采样点数
     * @return 匹配到的用户ID，未匹配到返回null
     */
    public String recognize(short[] pcmData, int sampleRate, float SIMILARITY_THRESHOLD) {

        // 将short[]转换为byte[]（16位PCM数据）
        byte[] byteData = shortToByteArray(pcmData);
        int numSamples = pcmData.length;
        // 提取实时语音的声纹特征
        float[] liveFeature = speekerID.extractAudioFeatureFromPCM(byteData, sampleRate, numSamples);
        if (liveFeature == null) {
            return "-1";
        }

        // 与所有已知用户的特征进行比对
        String bestMatchUserId = null;
        float highestSimilarity = 0;

        for (Map.Entry<String, float[]> entry : userFeatures.entrySet()) {
            String userId = entry.getKey();
            float[] storedFeature = entry.getValue();

            // 计算相似度
            float similarity = speekerID.compareFeatures(liveFeature, storedFeature);

            // 记录最高相似度的用户ID
            if (similarity > highestSimilarity) {
                highestSimilarity = similarity;
                bestMatchUserId = userId;
            }
        }

        /*
        if(highestSimilarity >= SIMILARITY_THRESHOLD) {
            Log.e(TAG, "highestSimilarity ：" + highestSimilarity + "  bestMatchUserId:" + bestMatchUserId);
        }else{
            Log.d(TAG, "highestSimilarity ：" + highestSimilarity + "  bestMatchUserId:" + bestMatchUserId);
        }*/

        // 只有当最高相似度超过阈值时才认为匹配成功
        return highestSimilarity >= SIMILARITY_THRESHOLD ? bestMatchUserId : "-1";
    }

    /**
     * 将short数组转换为byte数组（小端模式，符合大多数音频系统的存储方式）
     */
    private byte[] shortToByteArray(short[] shorts) {
        int length = shorts.length;
        byte[] bytes = new byte[length * 2];
        for (int i = 0; i < length; i++) {
            // 低8位
            bytes[i * 2] = (byte) (shorts[i] & 0xFF);
            // 高8位
            bytes[i * 2 + 1] = (byte) (shorts[i] >> 8);
        }
        return bytes;
    }


    /**
     * 释放资源
     */
    public void release() {
        speekerID.uninit();
        userFeatures.clear();
    }
}
