package com.patchx_main.patchx.util;

import android.content.Context;
import android.content.SharedPreferences;

public class SpHelper {

    private static final String FILE_NAME = "patchx_prefs";
    private static volatile SpHelper INSTANCE;
    private SharedPreferences sp;

    private SpHelper(Context context) {
        sp = context.getApplicationContext().getSharedPreferences(FILE_NAME, Context.MODE_PRIVATE);
    }

    public static SpHelper getInstance(Context context) {
        if (INSTANCE == null) {
            synchronized (SpHelper.class) {
                if (INSTANCE == null) {
                    INSTANCE = new SpHelper(context);
                }
            }
        }
        return INSTANCE;
    }
    public void putString(String key, String value) {
        sp.edit().putString(key, value).apply();
    }

    public void putInt(String key, int value) {
        sp.edit().putInt(key, value).apply();
    }

    public void putBoolean(String key, boolean value) {
        sp.edit().putBoolean(key, value).apply();
    }

    public void putFloat(String key, float value) {
        sp.edit().putFloat(key, value).apply();
    }

    public void putLong(String key, long value) {
        sp.edit().putLong(key, value).apply();
    }

    public String getString(String key, String defValue) {
        return sp.getString(key, defValue);
    }

    public int getInt(String key, int defValue) {
        return sp.getInt(key, defValue);
    }

    public boolean getBoolean(String key, boolean defValue) {
        return sp.getBoolean(key, defValue);
    }

    public float getFloat(String key, float defValue) {
        return sp.getFloat(key, defValue);
    }

    public long getLong(String key, long defValue) {
        return sp.getLong(key, defValue);
    }
    public void remove(String key) {
        sp.edit().remove(key).apply();
    }

    public void clear() {
        sp.edit().clear().apply();
    }
}
