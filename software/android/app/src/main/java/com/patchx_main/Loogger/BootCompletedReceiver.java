package  com.Loogger;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import androidx.work.OneTimeWorkRequest;
import androidx.work.WorkManager;
import java.util.concurrent.TimeUnit;

/**
 * 设备启动完成后触发，确保重启后立即保存缓存的日志
 */
public class BootCompletedReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) {
            // 1. 立即执行一次日志保存（处理关机前未保存的日志）
            OneTimeWorkRequest saveWork = new OneTimeWorkRequest.Builder(LogSaverWorker.class)
                    .setInitialDelay(5, TimeUnit.SECONDS) // 延迟5秒，避免系统刚启动资源紧张
                    .build();
            WorkManager.getInstance(context).enqueue(saveWork);

            // 2. 重新调度周期性任务（确保12小时周期继续生效）
            LogManager.getInstance(context).schedulePeriodicSave();
        }
    }
}
