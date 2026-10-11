package com.patchx.patchface;

import android.content.*;
import android.database.Cursor;
import android.database.sqlite.*;
import android.util.Log;

import java.nio.*;
import java.util.*;

/**
 * FaceDb: 简化的人脸特征数据库管理类
 * - 初始化时自动加载全库到内存
 * - 后续操作读写内存+同步到SQLite
 * - 主键为自增id
 */
public class FaceDb {

    private static final String TAG = "FaceDb";

    private static final String DB_NAME   = "face_db.sqlite";
    private static final int    DB_VER    = 1;
    private static final String TABLE     = "face_embeddings";
    private static final String COL_ID    = "id";      // 自增主键
    private static final String COL_FEAT  = "feature"; // BLOB(float32)

    private final SQLiteOpenHelper helper;

    // 内存缓存：id → feature
    private final Map<Long, float[]> memoryCache = new HashMap<>();

    public FaceDb(Context context) {
        this.helper = new Helper(context.getApplicationContext());
        loadAllIntoMemory(); // ✅ 初始化时直接加载到内存
    }

    public void close() {
        helper.close();
        memoryCache.clear();
    }
    private static final class Helper extends SQLiteOpenHelper {
        Helper(Context ctx) { super(ctx, DB_NAME, null, DB_VER); }

        @Override public void onCreate(SQLiteDatabase db) {
            db.execSQL("CREATE TABLE IF NOT EXISTS " + TABLE + " (" +
                    COL_ID   + " INTEGER PRIMARY KEY AUTOINCREMENT," +
                    COL_FEAT + " BLOB NOT NULL" +
                    ")");
        }

        @Override public void onUpgrade(SQLiteDatabase db, int oldV, int newV) {}
    }

    public static byte[] toBlob(float[] f) {
        if (f == null) return null;
        ByteBuffer bb = ByteBuffer.allocate(f.length * 4).order(ByteOrder.LITTLE_ENDIAN);
        for (float v : f) bb.putFloat(v);
        return bb.array();
    }

    public static float[] fromBlob(byte[] blob) {
        if (blob == null || blob.length % 4 != 0) return null;
        FloatBuffer fb = ByteBuffer.wrap(blob).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer();
        float[] out = new float[fb.remaining()];
        fb.get(out);
        return out;
    }

    /** 启动时加载数据库所有特征到内存 */
    private void loadAllIntoMemory() {
        memoryCache.clear();
        SQLiteDatabase db = helper.getReadableDatabase();
        Cursor c = null;
        try {
            c = db.query(TABLE, new String[]{COL_ID, COL_FEAT},
                    null, null, null, null, COL_ID);
            int idxId = c.getColumnIndexOrThrow(COL_ID);
            int idxFeat = c.getColumnIndexOrThrow(COL_FEAT);
            while (c.moveToNext()) {
                long id = c.getLong(idxId);
                float[] feat = fromBlob(c.getBlob(idxFeat));
                if (feat != null && feat.length > 0) {
                    memoryCache.put(id, feat);
                }
            }
            Log.i(TAG, "内存加载完成，总数=" + memoryCache.size());
        } catch (Exception e) {
            Log.e(TAG, "loadAllIntoMemory error", e);
        } finally {
            if (c != null) c.close();
        }
    }

    /** 插入新的人脸特征，返回自增ID，同时更新内存 */
    public synchronized long saveFeature(float[] feature) {
        if (feature == null || feature.length == 0) return -1;
        SQLiteDatabase db = helper.getWritableDatabase();
        ContentValues cv = new ContentValues();
        cv.put(COL_FEAT, toBlob(feature));
        try {
            long newId = db.insert(TABLE, null, cv);
            if (newId != -1) {
                memoryCache.put(newId, feature); // ✅ 同步到内存
            }
            Log.i(TAG, "saveFeature newId=" + newId);
            return newId;
        } catch (Exception e) {
            Log.e(TAG, "saveFeature error", e);
            return -1;
        }
    }

    /** 根据ID读取特征（优先从内存） */
    public float[] getFeature(long id) {
        return memoryCache.get(id);
    }

    /** 获取全部内存缓存（只读视图） */
    public Map<Long, float[]> getAllFeatures() {
        return Collections.unmodifiableMap(memoryCache);
    }

    /** 删除指定ID */
    public synchronized boolean deleteFeature(long id) {
        SQLiteDatabase db = helper.getWritableDatabase();
        try {
            int n = db.delete(TABLE, COL_ID + "=?", new String[]{String.valueOf(id)});
            if (n > 0) memoryCache.remove(id); // ✅ 同步删除
            return n > 0;
        } catch (Exception e) {
            Log.e(TAG, "deleteFeature error", e);
            return false;
        }
    }

    /** 清空数据库 + 内存 */
    public synchronized boolean clearAll() {
        SQLiteDatabase db = helper.getWritableDatabase();
        try {
            db.delete(TABLE, null, null);
            memoryCache.clear();
            return true;
        } catch (Exception e) {
            Log.e(TAG, "clearAll error", e);
            return false;
        }
    }

    /** 当前人脸总数（内存缓存） */
    public int count() {
        return memoryCache.size();
    }
}
