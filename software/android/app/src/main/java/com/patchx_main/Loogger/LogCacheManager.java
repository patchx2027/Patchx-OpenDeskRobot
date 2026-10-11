package  com.Loogger;

import android.content.Context;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.locks.ReentrantLock;

/**
 * 日志缓存管理器：将日志临时保存到文件，确保关机后不丢失
 */
public class LogCacheManager {
    private static final String CACHE_FILE_NAME = "temp_log_cache.txt";
    private static LogCacheManager instance;
    private final File cacheFile;
    private final ReentrantLock lock = new ReentrantLock(); // 线程安全

    private LogCacheManager(Context context) {
        // 缓存文件放在应用私有目录（Android 11 无需权限）
        cacheFile = new File(context.getFilesDir(), CACHE_FILE_NAME);
    }

    public static synchronized LogCacheManager getInstance(Context context) {
        if (instance == null) {
            instance = new LogCacheManager(context.getApplicationContext());
        }
        return instance;
    }

    /**
     * 写入日志到缓存文件
     */
    public void writeToCache(String log) {
        lock.lock();
        try (FileOutputStream fos = new FileOutputStream(cacheFile, true)) {
            // 每条日志末尾加换行，方便后续读取
            fos.write((log + "\n").getBytes(StandardCharsets.UTF_8));
        } catch (IOException e) {
            e.printStackTrace();
        } finally {
            lock.unlock();
        }
    }

    /**
     * 读取并清空缓存文件中的日志
     */
    public String readAndClearCache() {
        lock.lock();
        StringBuilder sb = new StringBuilder();
        try (FileInputStream fis = new FileInputStream(cacheFile)) {
            byte[] buffer = new byte[1024];
            int len;
            while ((len = fis.read(buffer)) != -1) {
                sb.append(new String(buffer, 0, len, StandardCharsets.UTF_8));
            }
            // 读取后清空缓存
            cacheFile.delete();
            cacheFile.createNewFile();
        } catch (IOException e) {
            e.printStackTrace();
        } finally {
            lock.unlock();
        }
        return sb.toString();
    }

    /**
     * 检查是否有缓存的日志
     */
    public boolean hasCachedLogs() {
        return cacheFile.exists() && cacheFile.length() > 0;
    }
}
