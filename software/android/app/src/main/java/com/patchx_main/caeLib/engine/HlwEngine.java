package com.patchx_main.caeLib.engine;

import android.content.Context;
import android.util.Log;

import com.iflytek.iflyos.cae.CAE;
import com.patchx_main.caeLib.bean.CheckResultBean;
import com.patchx_main.caeLib.bean.WakeInfo;
import com.patchx_main.caeLib.utils.FileUtils;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.Map;


/**
 * 远距离引擎【3-5m】
 */
public class HlwEngine extends BaseEngine {
    private static final String configCAE_RES = "CAE_RES";
    private static final String configIVW_RES = "IVW_RES";
    private static final String configINI_RES = "INI_RES";
    private static final String configPARAM_RES = "PARAM_RES";
    private static final String configCAE_DIST = "CAE_DIST";
    private static final String iniHlwName = "hlw.ini";
    private static final String configMicNum = "nMicNum";
    private static final String assetParentPath = "hlw/";
    private static final String TAG = "HlwEngine";

    static {
        CAE.loadLib("hlw");
    }


    @Override
    protected WakeInfo parseWakeInfo(String s) {
        // {"cur_ms":288768, "start_ms":287290, "end_ms":288310, "beam":1, "physical":1, "similar":1498.000000, "similar_thresh":900.000000, "power":1210436681728.000000, "angle":75.000000, "keyword":"ni3 hao3 hua1 sheng1"}
        JSONObject jsonObject = null;
        WakeInfo wakeInfo = new WakeInfo();
        try {
            jsonObject = new JSONObject(s);
            int beam = jsonObject.optInt("physical");
            int angle = jsonObject.optInt("angle");
            int similar = jsonObject.optInt("similar");
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
        CheckResultBean checkResult = new CheckResultBean();
        List<String> keyList = Arrays.asList(configCAE_RES, configIVW_RES,configINI_RES,configPARAM_RES,configCAE_DIST);
        Map<String, String> stringStringMap = FileUtils.readAssetValueByKey(context,
                assetParentPath + iniHlwName, keyList);
        if (stringStringMap != null && stringStringMap.size() == keyList.size()) {
            try {
                configParentPath = context.getFilesDir().getAbsolutePath();

                String cae_res_path = stringStringMap.get(configCAE_RES);
                checkResource(context, configParentPath+cae_res_path, assetParentPath);
                String ivw_res_path = stringStringMap.get(configIVW_RES);
                checkResource(context, configParentPath+ivw_res_path, assetParentPath);
                String ini_res_path = stringStringMap.get(configINI_RES);
                checkResource(context, configParentPath+ini_res_path, assetParentPath);
                String param_res_path = stringStringMap.get(configPARAM_RES);
                checkResource(context, configParentPath+param_res_path, assetParentPath);
                String cae_dist = stringStringMap.get(configCAE_DIST);

                File fileIni = null ;
                FileOutputStream fileOutputStream =null ;
                try {
                    fileIni = new File(configParentPath + new File(ini_res_path).getParent(), "hlw_real.ini");
                    fileIni.createNewFile();
                    fileOutputStream = new FileOutputStream(fileIni);
                    fileOutputStream.write(String.format("CAE_RES = %s\n",configParentPath+cae_res_path).getBytes());
                    fileOutputStream.write(String.format(Locale.getDefault(),"CAE_DIST = %d\n",Integer.parseInt(cae_dist)).getBytes());
                    fileOutputStream.write(String.format("IVW_RES = %s\n",configParentPath+ivw_res_path).getBytes());
                    fileOutputStream.close();
                }finally {
                    if (fileOutputStream != null){
                        fileOutputStream.close();
                    }
                }

                // step1 : 根据配置资源进行初始化
                int isInit = CAE.CAENew(fileIni.getAbsolutePath(), configParentPath+param_res_path, mCAEListener);
                if (isInit == 0) {
                    // step2 : 根据授权码进行授权，授权正确后，即可以使用
                    String version = CAE.CAEGetVersion();
        Log.d(TAG, "Voice authorization initialized.");
                    int auth = CAE.CAEAuth(sn);
                    if (auth >= 0) {
                        checkResult.setState(1);
                        List<String> keyListMicNum = Collections.singletonList(configMicNum);
                        Map<String, String> MicNumMap = FileUtils.readAssetValueByKey(context,
                                assetParentPath + new File(cae_res_path).getName(), keyListMicNum);
                        checkResult.setCheckResult(String.format(Locale.getDefault(), "%s,%s,micNum=%s",  version,cae_res_path, MicNumMap.get(configMicNum)));
                    } else {
                        checkResult.setState(0);
                        checkResult.setCheckResult("cae auth error, code  = " + auth);
                    }
                } else {
                    checkResult.setState(0);
                    checkResult.setCheckResult("cae init error,please check res file " + (configParentPath+ini_res_path) + ";" + (configParentPath+param_res_path));
                }

            } catch (IOException ignored) {
                checkResult.setState(0);
                checkResult.setCheckResult("copy res file error " + ignored);
            }
        } else {
            checkResult.setState(0);
            checkResult.setCheckResult("hlw.ini config error");
        }
        return checkResult;
    }


    @Override
    public void setBeam(int beam) {
        int i = CAE.CAESetRealBeam(beam);
        Log.e(TAG, "setBeam: " + i + "   " + beam);
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
