package  com.Loogger;

import android.content.Context;
import com.orhanobut.logger.FormatStrategy;
import com.orhanobut.logger.LogAdapter;
import com.orhanobut.logger.Logger;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * 自定义Logger适配器：同时输出日志到控制台和本地缓存
 */
public class FileLoggerAdapter implements LogAdapter {
    private final FormatStrategy formatStrategy;
    private final LogCacheManager logCache;
    private final SimpleDateFormat sdf = new SimpleDateFormat(
            "yyyy-MM-dd HH:mm:ss.SSS", Locale.getDefault()
    );

    public FileLoggerAdapter(Context context, FormatStrategy strategy) {
        this.formatStrategy = strategy;
        this.logCache = LogCacheManager.getInstance(context);
    }

    @Override
    public boolean isLoggable(int priority, String tag) {
        return true; // 所有级别日志都记录
    }

    @Override
    public void log(int priority, String tag, String message) {
        // 1. 输出到控制台（开源Logger的默认行为）
        formatStrategy.log(priority, tag, message);

        // 2. 格式化日志并写入缓存（包含时间戳、级别、标签）
        String level = getLevelString(priority);
        String time = sdf.format(new Date());
        String formattedLog = String.format(
                Locale.getDefault(),
                "[%s] [%s] [%s]: %s",
                time, level, tag, message
        );
        logCache.writeToCache(formattedLog);
    }

    // 将日志级别转为字符串（如DEBUG -> D）
    private String getLevelString(int priority) {
        switch (priority) {
            case android.util.Log.VERBOSE: return "V";
            case android.util.Log.DEBUG: return "D";
            case android.util.Log.INFO: return "I";
            case android.util.Log.WARN: return "W";
            case android.util.Log.ERROR: return "E";
            default: return "?";
        }
    }
}
