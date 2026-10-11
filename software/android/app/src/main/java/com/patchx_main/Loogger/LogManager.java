package  com.Loogger;

import android.content.Context;

import androidx.work.OneTimeWorkRequest;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;
import com.orhanobut.logger.Logger;
import com.orhanobut.logger.PrettyFormatStrategy;
import java.util.concurrent.TimeUnit;

/**
 * 日志管理器：初始化Logger和调度定期保存任务
 */
public class LogManager {
    private static LogManager instance;
    private final Context context;

    private LogManager(Context context) {
        this.context = context.getApplicationContext();
    }

    public static synchronized LogManager getInstance(Context context) {
        if (instance == null) {
            instance = new LogManager(context);
        }
        return instance;
    }

    /**
     * 初始化日志系统
     */
    public void init() {
        // 1. 配置开源Logger的格式（控制台输出）
        PrettyFormatStrategy formatStrategy = PrettyFormatStrategy.newBuilder()
                .showThreadInfo(false) // 不显示线程信息
                .methodCount(1) // 显示1行方法调用栈
                .methodOffset(0)
                .tag("MyApp") // 全局标签
                .build();

        // 2. 设置自定义适配器（同时输出到控制台和缓存）
        Logger.addLogAdapter(new FileLoggerAdapter(context, formatStrategy));

        // 3. 调度周期性保存任务（每12小时）
        schedulePeriodicSave();

        // 4. 检查是否有未保存的日志（如应用崩溃后重启）
        checkAndSaveCachedLogs();
    }

    /**
     * 调度每12小时执行一次日志保存
     */
    public void schedulePeriodicSave() {
        // 周期性任务，最小间隔为12小时（WorkManager要求周期任务至少15分钟）
        PeriodicWorkRequest periodicWork = new PeriodicWorkRequest.Builder(
                LogSaverWorker.class,
                10, TimeUnit.MINUTES
        )
                .setInitialDelay(0, TimeUnit.SECONDS) // 立即开始第一个任务
                .build();

        WorkManager.getInstance(context).enqueue(periodicWork);
    }

    /**
     * 检查并保存可能遗留的缓存日志（如应用异常退出后）
     */
    private void checkAndSaveCachedLogs() {
        LogCacheManager cacheManager = LogCacheManager.getInstance(context);
        if (cacheManager.hasCachedLogs()) {
            OneTimeWorkRequest saveWork = new OneTimeWorkRequest.Builder(LogSaverWorker.class)
                    .build();
            WorkManager.getInstance(context).enqueue(saveWork);
        }
    }
}
