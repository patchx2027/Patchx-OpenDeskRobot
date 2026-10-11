package com.patchx_main.ability.util;

import android.content.Context;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.io.InputStream;

public class FileUtil {
    public static String readAssets(Context context, String assetsName) {
        InputStream inputStream = null;
        try {
            inputStream = context.getAssets().open(assetsName);
            int available = inputStream.available();
            byte[] bytes = new byte[available];
            int read = inputStream.read(bytes);
            return new String(bytes, 0, read).trim();
        } catch (IOException e) {
            e.printStackTrace();
        } finally {
            if (inputStream != null) {
                try {
                    inputStream.close();
                } catch (IOException e) {
                    e.printStackTrace();
                }
            }
        }
        return null;
    }

    public static String getAppId(String cfg) {
        JSONObject paramsJson = null;
        try {
            paramsJson = new JSONObject(cfg);
            String login = paramsJson.optString("login");
            JSONObject loginObject = new JSONObject(login);
            return loginObject.optString("appid");
        } catch (JSONException e) {
            e.printStackTrace();
        }
        return "";
    }
}
