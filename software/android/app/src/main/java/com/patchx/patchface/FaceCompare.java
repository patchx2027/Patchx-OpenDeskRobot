package com.patchx.patchface;
import android.util.Log;

import java.util.Map;

public class FaceCompare {
    private static final String TAG = "FaceCompare";
    private static volatile FaceCompare INSTANCE;
    public static FaceCompare getInstance(){
        if (INSTANCE == null) {
            synchronized (FaceCompare.class) {
                if (INSTANCE == null) {
                    INSTANCE = new FaceCompare();
                }
            }
        }
        return INSTANCE;
    }

    public long faceCompareAction(float[] newFeat, FaceDb faceDb){
        if (newFeat == null || newFeat.length == 0) {
            Log.i("测试====FaceCompare", "特征为空，忽略");
            return -1;
        }

        Map<Long, float[]> map = faceDb.getAllFeatures();
        if (map.isEmpty()){
            Log.i("测试====FaceCompare", "数据库为空");
            long id =  faceDb.saveFeature(newFeat);
            return id;
        }
        for (Map.Entry<Long, float[]> entry : map.entrySet()){
            long id = entry.getKey();
            float[] feat = entry.getValue();
            float value = cosineSimilarity(newFeat,feat);
            if (value > 0.75){
                Log.i(TAG,"测试====FaceCompare===cosineSimilarity===余弦相似度===="+String.valueOf(value));
            }else{
                Log.i(TAG,"测试====FaceCompare=余弦相似度="+String.valueOf(value));
            }

        }
        return -1;
    }


    private float cosineSimilarity(float[] a, float[] b) {
        if (a == null || b == null || a.length != b.length) {
            return -1f;
        }
        double dot = 0, na = 0, nb = 0;
        for (int i = 0; i < a.length; i++) {
            dot += a[i] * b[i];
            na  += a[i] * a[i];
            nb  += b[i] * b[i];
        }
        if (na == 0 || nb == 0){
            return -1f;
        }
        return (float) (dot / Math.sqrt(na * nb));
    }
}
