package com.patchx_main.caeLib.engine;

import android.content.Context;
import android.util.Log;

import androidx.core.app.NavUtils;

import com.iflytek.iflyos.cae.CAE;
import com.patchx_main.caeLib.bean.CheckResultBean;
import com.patchx_main.caeLib.bean.WakeInfo;
import com.patchx_main.caeLib.utils.FileUtils;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.DataInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.FileReader;
import java.io.IOException;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.Map;

@Deprecated
public class VtnEngine extends BaseEngine {
    private static final String TAG = "VtnEngine";
    private static final String configPub_RES = "rsa_pub";
    private static final String configPri_RES = "rsa_pri";
    private static final String configIVW_RES = "res_path";
    private static final String configINI_RES = "vtn_ini_path";
    private static final String iniVtnName = "vtn.ini";
    private static final String assetParentPath = "vtn/";

    static {
        CAE.loadLib("vtn");
    }

    @Override
    protected WakeInfo parseWakeInfo(String s) {
        // {"ivw":{"start_ms":7550,"end_ms":8650,"beam":1,"physical":1,"score":1208.0,"power":47620743168.0,"angle":70.0,"keyword":"xiao3 wei1 xiao3 wei1"}}
        JSONObject jsonObject = null;
        WakeInfo wakeInfo = new WakeInfo();
        try {
            jsonObject = new JSONObject(s);
            JSONObject ivw = jsonObject.getJSONObject("ivw");
            int beam = ivw.optInt("physical");
            int angle = ivw.optInt("angle");
            int similar = ivw.optInt("score");
            wakeInfo.setBeam(beam);
            wakeInfo.setAngle(angle);
            wakeInfo.setScore(similar);
        } catch (JSONException e) {
            e.printStackTrace();
            wakeInfo.setAngle(0);
            wakeInfo.setBeam(0);
        }
        return wakeInfo;
    }

    @Override
    public CheckResultBean init(Context context, String sn) {
        //res check
        Log.d(TAG, "Voice authorization initialized.");
        CheckResultBean checkResult = new CheckResultBean();
        List<String> keyList = Arrays.asList(configPub_RES, configPri_RES, configIVW_RES,configINI_RES);
        Map<String, String> stringStringMap = FileUtils.readAssetValueByKey(context,
                assetParentPath + iniVtnName, keyList);
        if (stringStringMap != null && stringStringMap.size() == keyList.size()) {
            try {
                configParentPath = context.getFilesDir().getAbsolutePath() ;

                String pub_res_path = stringStringMap.get(configPub_RES);
                checkResource(context, configParentPath+pub_res_path, assetParentPath);
                String pri_res_path = stringStringMap.get(configPri_RES);
                checkResource(context, configParentPath+pri_res_path, assetParentPath);
                String ivw_res_path = stringStringMap.get(configIVW_RES);
                checkResource(context, configParentPath+ivw_res_path, assetParentPath);

                String ini_res_path = stringStringMap.get(configINI_RES);
                checkResource(context, configParentPath+ini_res_path, assetParentPath);

                File fileRealIni = null ;
                BufferedReader reader = null ;
                FileOutputStream fileOutputStream = null ;
                try {
                    File file = new File(configParentPath + ini_res_path);
                    reader = new BufferedReader(new FileReader(file));
                    StringBuilder stringBuilder = new StringBuilder();
                    while (true){
                        String s = reader.readLine();
                        if (s == null){
                            break;
                        }else{
                            if (s.contains(configPub_RES)){
                                stringBuilder.append(String.format("%s=%s",configPub_RES,configParentPath+pub_res_path)).append("\n");
                            }else if (s.contains(configPri_RES)){
                                stringBuilder.append(String.format("%s=%s",configPri_RES,configParentPath+pri_res_path)).append("\n");
                            }else if (s.contains(configIVW_RES)){
                                stringBuilder.append(String.format("%s=%s",configIVW_RES,configParentPath+ivw_res_path)).append("\n");
                            }else{
                                stringBuilder.append(s).append("\n");
                            }
                        }
                    }
                    fileRealIni = new File(file.getParentFile(), "vtn_real.ini");
                    fileOutputStream = new FileOutputStream(fileRealIni);
                    fileOutputStream.write(stringBuilder.toString().getBytes());
                    fileOutputStream.close();
                }finally {
                    if (reader!= null){
                        reader.close();
                    }
                    if (fileOutputStream != null){
                        fileOutputStream.close();
                    }
                }

                int isInit = CAE.CAENew(sn, fileRealIni.getAbsolutePath(), mCAEListener);
                if (isInit == 0) {
                    String version = CAE.CAEGetVersion();
                    Log.d(TAG, "init: version =" + version);
                    checkResult.setState(1);
                    CAE.CAESetShowLog(5);
                    checkResult.setCheckResult(String.format(Locale.getDefault(), "[%s,%s,%s,%s]", pub_res_path, pri_res_path, ivw_res_path, version));
                } else {
                    checkResult.setState(0);
                    checkResult.setCheckResult("vtn auth error,please check res file,code="+isInit);
                }

            } catch (IOException ignored) {
                checkResult.setState(0);
                checkResult.setCheckResult("copy res file error");
            }
        } else {
            checkResult.setState(0);
            checkResult.setCheckResult("vtn.ini config error");
        }
        return checkResult;
    }

    @Override
    public void setBeam(int beam) {
        CAE.CAESetRealBeam(beam);
    }

    @Override
    public void writeAudio(byte[] audio, int len) {
        CAE.CAEAudioWrite(audio, len);
    }

    @Override
    public void setShowLog(boolean show) {
        CAE.CAESetShowLog(show ? 0 : 1);
    }

    @Override
    public void onDestroy() {
        CAE.CAEDestory();
    }
}
