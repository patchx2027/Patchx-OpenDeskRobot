package  com.Loogger;

import android.content.Context;
import androidx.annotation.NonNull;
import androidx.work.Worker;
import androidx.work.WorkerParameters;
import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * 日志保存Worker：定期将缓存的日志写入正式文件
 */
public class LogSaverWorker extends Worker {
    private static final String LOG_DIR = "app_logs"; // 日志目录名
    private final LogCacheManager logCache;

    public LogSaverWorker(
            @NonNull Context context,
            @NonNull WorkerParameters params
    ) {
        super(context, params);
        logCache = LogCacheManager.getInstance(context);
    }

    @NonNull
    @Override
    public Result doWork() {
        // 1. 检查是否有缓存的日志
        if (!logCache.hasCachedLogs()) {
            return Result.success();
        }

        // 2. 读取缓存日志
        String cachedLogs = logCache.readAndClearCache();
        if (cachedLogs.isEmpty()) {
            return Result.success();
        }

        // 3. 按日期创建日志文件（如 log_2023-07-16.txt）
        String date = new SimpleDateFormat("yyyy-MM-dd", Locale.getDefault())
                .format(new Date());
        File logDir = new File(getApplicationContext().getExternalFilesDir(null), LOG_DIR);
        if (!logDir.exists()) {
            logDir.mkdirs(); // 创建目录（Android 11 无需权限）
        }
        File logFile = new File(logDir, "log_" + date + ".txt");

        // 4. 写入日志到文件
        try (FileWriter writer = new FileWriter(logFile, true)) {
            // 追加写入，并添加分隔符（区分不同批次的日志）
            writer.write("\n=== 日志批次：" +
                    new SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(new Date()) +
                    " ===\n");
            writer.write(cachedLogs);
            return Result.success();
        } catch (IOException e) {
            e.printStackTrace();
            // 失败后重试（最多重试3次）
            return Result.retry();
        }
    }
}
