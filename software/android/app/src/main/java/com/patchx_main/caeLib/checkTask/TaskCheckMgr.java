package com.patchx_main.caeLib.checkTask;


import com.patchx_main.caeLib.checkTask.base.BaseCheckTask;
import com.patchx_main.caeLib.checkTask.base.ICheckTaskCallBack;

import java.util.ArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.LinkedBlockingQueue;

public class TaskCheckMgr {
    private static TaskCheckMgr instance = new TaskCheckMgr();

    private TaskCheckMgr() {
    }

    public static TaskCheckMgr getInstance() {
        return instance;
    }

    private LinkedBlockingQueue<BaseCheckTask> blockingQueue = new LinkedBlockingQueue<>(10);
    private ExecutorService executor = Executors.newSingleThreadExecutor();
    private ICheckTaskCallBack mCheckTaskCallBack = null;
    private volatile boolean isStop = false;

    public void addTask(ArrayList<BaseCheckTask> baseCheckTaskList) {
        for (BaseCheckTask it : baseCheckTaskList) {
            if (!blockingQueue.contains(it)) {
                blockingQueue.add(it);
            }
        }
    }

    public void startCheckTask(ICheckTaskCallBack iCheckTaskCallBack) {
        isStop = false;
        mCheckTaskCallBack = iCheckTaskCallBack;
        executor.execute(() -> {
            while (blockingQueue.size() > 0 && !isStop) {
                BaseCheckTask take = null;
                try {
                    take = blockingQueue.take();
                    take.setICheckCallBack(mCheckTaskCallBack);
                    take.run();
                } catch (InterruptedException e) {
                    e.printStackTrace();
                }
            }
            if (mCheckTaskCallBack != null) {
                mCheckTaskCallBack.onAllTaskCheckEnd();
                mCheckTaskCallBack = null;
            }
        });
    }

    public void stopCheCkTask() {
        isStop = true;
        executor.shutdownNow();
    }
}
