package com.patchx_main.patchx.manage;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.content.pm.PackageManager;
import android.util.Log;

import androidx.core.app.ActivityCompat;

import com.patchx_main.caeLib.VoiceEngineMgr;
import com.patchx_main.caeLib.bean.CheckResultBean;
import com.patchx_main.caeLib.bean.PcmDevice;
import com.patchx_main.caeLib.checkTask.CaeAuthCheck;
import com.patchx_main.caeLib.checkTask.CardNumberCheck;
import com.patchx_main.caeLib.checkTask.CardOpenCheck;
import com.patchx_main.caeLib.checkTask.CardPermissionCheck;
import com.patchx_main.caeLib.checkTask.NetCheck;
import com.patchx_main.caeLib.checkTask.PlatformMicCheck;
import com.patchx_main.caeLib.checkTask.SystemTimeCheck;
import com.patchx_main.caeLib.checkTask.TaskCheckMgr;
import com.patchx_main.caeLib.checkTask.base.BaseCheckTask;
import com.patchx_main.caeLib.checkTask.base.ICheckTaskCallBack;
import com.patchx_main.caeLib.dataAdapter.BothlentAdapter;
import com.patchx_main.caeLib.engine.HlwNearEngine;
import com.patchx_main.caeLib.record.BothlentRecord;
import com.patchx_main.caeLib.utils.FileUtils;
import com.patchx_main.caeLib.utils.ShellUtil;
import com.patchx_main.patchx.event.CheckEvent;
import com.patchx_main.patchx.mqtt.UnifiedRobotState;

import org.greenrobot.eventbus.EventBus;

import java.util.ArrayList;
import java.util.List;

public class CheckManager {
    private static final String TAG = "CheckActivity";

    private static CheckManager INSTANCE;

    private final String[] permissionArray = new String[]{
            Manifest.permission.WRITE_EXTERNAL_STORAGE,
            Manifest.permission.READ_EXTERNAL_STORAGE,
            Manifest.permission.ACCESS_WIFI_STATE,
            Manifest.permission.CHANGE_WIFI_STATE,
            Manifest.permission.INTERNET,
            Manifest.permission.ACCESS_NETWORK_STATE,
            Manifest.permission.RECORD_AUDIO,
            Manifest.permission.REORDER_TASKS,
            Manifest.permission.GET_TASKS,
            Manifest.permission.READ_PHONE_STATE
    };

    private ArrayList<BaseCheckTask> taskList = new ArrayList<>();

    private List<CheckResultBean> checkList = new ArrayList<>();


    public static CheckManager getInstance () {
        if (INSTANCE == null) {
            synchronized (CheckManager.class) {
                if (INSTANCE == null) {
                    INSTANCE = new CheckManager();
                }
            }
        }
        return INSTANCE;
    }
    public CheckManager(){

    }
    private float translateValue(int value) {
        return (float) (value * 1.0 / 100f);
    }

    public void startCheckTaskAction(Activity activity){
        try{
            if (this.checkPermissions(activity)){
                //1. 初始化降噪算法
                VoiceEngineMgr instance = VoiceEngineMgr.getInstance();
                VoiceEngineMgr.setSoftGain(translateValue(60));
                BothlentRecord baseRecord = new BothlentRecord(new PcmDevice(ShellUtil.fetchCard()));
                instance.init(activity, new HlwNearEngine(), baseRecord, new BothlentAdapter());
                //2. 添加自检任务
                Context applicationContext = activity.getApplicationContext();
                if (baseRecord.isNeedCheck()) {
                    taskList.add(new CardNumberCheck(applicationContext));
                    taskList.add(new CardPermissionCheck(applicationContext));
                }
                taskList.add(new NetCheck(applicationContext));
                taskList.add(new PlatformMicCheck(applicationContext));
                taskList.add(new SystemTimeCheck(applicationContext));
                taskList.add(new CaeAuthCheck(applicationContext, FileUtils.getAssetsContent(activity, "auth.ini")));
                taskList.add(new CardOpenCheck(applicationContext));
                //3. 开始自检
                startCheckTask();
            }
        }catch (Exception e){
            e.printStackTrace();
        }
    }
    private void startCheckTask() {
        TaskCheckMgr instance = TaskCheckMgr.getInstance();
        instance.addTask(taskList);
        instance.startCheckTask(new ICheckTaskCallBack() {
            @Override
            public void onAllTaskCheckEnd() {
                boolean isCheckSuccess = true;
                for (BaseCheckTask it : taskList) {
                    if (!it.isCheckSuccess()) {
                        isCheckSuccess = false;
                    }
                }
                if (isCheckSuccess) {
                    UnifiedRobotState.i().setSoundCard("ok");
                    Log.d(TAG,"测试====鱼亮检测===成功==可以唤醒");
                    EventBus.getDefault().post(new CheckEvent("success",""));
                }else {
                    UnifiedRobotState.i().setSoundCard("error");
                    Log.e(TAG,"测试====鱼亮检测===失败");
                    EventBus.getDefault().post(new CheckEvent("fail",""));
                }
            }
            @Override
            public void onCheckStart(CheckResultBean CheckResult) {
                Log.e(TAG, "onCheckStart: " + CheckResult);
                checkList.add(CheckResult);
            }
            @Override
            public void onCheckEnd(CheckResultBean CheckResult) {
                Log.e(TAG, "onCheckEnd: " + CheckResult);
            }
        });
    }
    /**
     * 权限检查
     */
    private boolean checkPermissions(Activity activity) {
        try{
            ArrayList<String> list = new ArrayList<>();
            for (String it : permissionArray) {
                if (ActivityCompat.checkSelfPermission(activity, it) != PackageManager.PERMISSION_GRANTED) {
                    list.add(it);
                }
            }
            if (list.size() > 0) {
                String[] array = new String[list.size()];
                for (int i = 0; i < list.size(); i++) {
                    array[i] = list.get(i);
                }
                ActivityCompat.requestPermissions(activity, array, 1);
                return false;
            } else {
                return true;
            }
        }catch (Exception e){
            e.printStackTrace();
            return false;
        }
    }
}
