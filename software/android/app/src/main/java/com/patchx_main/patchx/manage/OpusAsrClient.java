package com.patchx_main.patchx.manage;

import okhttp3.*;
import com.google.gson.Gson;
import com.google.gson.annotations.SerializedName;

import java.io.IOException;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * 安卓端Opus语音识别客户端
 * 功能：1. 处理Opus帧格式（加2字节小端序帧长前缀） 2. 发送Opus到服务端 3. 轮询获取识别结果
 */
public class OpusAsrClient {
    // 常量配置（与Python代码对齐）
    private static final String TAG = "OpusAsrClient";
    private static final int CONNECT_TIMEOUT = 10;  // 连接超时（秒）
    private static final int READ_TIMEOUT = 10;     // 读取超时（秒）
    private static final int POLL_INTERVAL = 200;   // 轮询间隔（毫秒，对应Python的0.2秒）
    private static final int MAX_POLL_TIMES = 10;   // 最大轮询次数（对应Python的10次）

    // 成员变量
    private final OkHttpClient okHttpClient;
    private final String baseUrl;  // 服务端基础URL（如 http://192.168.1.100:8000）
    private final Gson gson;
    private final ExecutorService executorService;  // 子线程池（处理轮询）

    // ==================== 外部回调接口 ====================
    /**
     * 获取TaskID的回调（发送Opus后触发）
     */
    public interface OnTaskIdListener {
        void onSuccess(String taskId);  // 成功获取TaskID
        void onFailure(String errorMsg); // 失败（网络错误/业务错误）
    }

    /**
     * 获取ASR识别结果的回调（轮询后触发）
     */
    public interface OnAsrResultListener {
        void onSuccess(String asrResult); // 识别成功（返回文本）
        void onFailure(String errorMsg);  // 失败（超时/网络错误/识别失败）
    }

    // ==================== 构造方法 ====================
    /**
     * 初始化客户端
     * @param baseUrl 服务端基础URL（例：http://192.168.1.100:8000，不可用localhost）
     */
    public OpusAsrClient(String baseUrl) {
        this.baseUrl = baseUrl;
        // 初始化OkHttp（设置超时）
        this.okHttpClient = new OkHttpClient.Builder()
                .connectTimeout(CONNECT_TIMEOUT, TimeUnit.SECONDS)
                .readTimeout(READ_TIMEOUT, TimeUnit.SECONDS)
                .build();
        // 初始化Gson（解析JSON响应）
        this.gson = new Gson();
        // 初始化单线程池（处理轮询，避免多线程混乱）
        this.executorService = Executors.newSingleThreadExecutor();
    }

    // ==================== 核心方法：发送Opus到服务端 ====================
    /**
     * 发送Opus数据到服务端的 /upload-opus 接口，获取TaskID
     * @param opusData 完整Opus数据（需先通过 wrapOpusFrames 处理帧前缀）
     * @param listener 结果回调
     */
    public void sendOpusToServer(byte[] opusData, OnTaskIdListener listener) {
        // 校验输入
        if (opusData == null || opusData.length == 0) {
            if (listener != null) listener.onFailure("Opus数据为空");
            return;
        }

        // 1. 构建请求（POST + 二进制流）
        String uploadUrl = baseUrl + "/upload-opus";
        RequestBody requestBody = RequestBody.create(
                MediaType.parse("application/octet-stream"), // 与Python的Content-Type一致
                opusData
        );
        Request request = new Request.Builder()
                .url(uploadUrl)
                .post(requestBody)
                .build();

        // 2. 异步发送请求（OkHttp的enqueue）
        okHttpClient.newCall(request).enqueue(new Callback() {
            @Override
            public void onFailure(Call call, IOException e) {
                // 网络错误（如连接超时、服务端不可达）
                if (listener != null) {
                    listener.onFailure("发送Opus失败：" + e.getMessage());
                }
            }

            @Override
            public void onResponse(Call call, Response response) throws IOException {
                if (!response.isSuccessful() || response.body() == null) {
                    // HTTP状态码非200（如404、500）
                    if (listener != null) {
                        listener.onFailure("服务器错误：" + response.code());
                    }
                    return;
                }

                // 3. 解析JSON响应（对应Python的返回格式）
                String responseStr = response.body().string();
                UploadResponse uploadResp = gson.fromJson(responseStr, UploadResponse.class);

                if (uploadResp == null || uploadResp.code != 0 || uploadResp.data == null) {
                    // 业务错误（如code≠0）
                    String errMsg = uploadResp != null ? uploadResp.msg : "响应解析失败";
                    if (listener != null) listener.onFailure("上传失败：" + errMsg);
                } else {
                    // 成功获取TaskID
                    if (listener != null) {
                        listener.onSuccess(uploadResp.data.taskId);
                    }
                }
            }
        });
    }

    // ==================== 核心方法：轮询获取识别结果 ====================
    /**
     * 根据TaskID轮询 /get-result/{taskId} 接口，获取ASR结果
     * @param taskId 发送Opus时获取的任务ID
     * @param listener 结果回调
     */
    public void getAsrResult(String taskId, OnAsrResultListener listener) {
        // 校验输入
        if (taskId == null || taskId.isEmpty()) {
            if (listener != null) listener.onFailure("TaskID为空");
            return;
        }

        // 子线程执行轮询（避免阻塞主线程）
        executorService.execute(() -> {
            int pollCount = 0;
            String finalResult = null;
            String errorMsg = null;

            while (pollCount < MAX_POLL_TIMES) {
                try {
                    // 1. 构建轮询请求（GET）
                    String resultUrl = baseUrl + "/get-result/" + taskId;
                    Request request = new Request.Builder()
                            .url(resultUrl)
                            .get()
                            .build();

                    // 2. 同步请求（子线程中可安全使用）
                    Response response = okHttpClient.newCall(request).execute();
                    if (!response.isSuccessful() || response.body() == null) {
                        errorMsg = "服务器错误：" + response.code();
                        break;
                    }

                    // 3. 解析JSON响应
                    String responseStr = response.body().string();
                    ResultResponse resultResp = gson.fromJson(responseStr, ResultResponse.class);

                    if (resultResp == null || resultResp.code != 0 || resultResp.data == null) {
                        errorMsg = resultResp != null ? resultResp.msg : "结果解析失败";
                        break;
                    }

                    // 4. 判断任务状态（对应Python的status）
                    String status = resultResp.data.status;
                    switch (status) {
                        case "done":
                            // 识别完成，获取结果
                            finalResult = resultResp.data.asrResult;
                            pollCount = MAX_POLL_TIMES; // 退出循环
                            break;
                        case "failed":
                            // 识别失败
                            errorMsg = "识别失败：" + resultResp.data.asrResult;
                            pollCount = MAX_POLL_TIMES;
                            break;
                        default:
                            // pending/processing，继续轮询
                            pollCount++;
                            Thread.sleep(POLL_INTERVAL); // 等待间隔
                            break;
                    }
                } catch (IOException e) {
                    // 网络错误
                    errorMsg = "轮询失败：" + e.getMessage();
                    break;
                } catch (InterruptedException e) {
                    // 线程被中断
                    errorMsg = "轮询被中断：" + e.getMessage();
                    Thread.currentThread().interrupt(); // 恢复中断状态
                    break;
                }
            }

            // 5. 回调结果（需确保在主线程？可由调用方处理，此处直接回调）
            if (finalResult != null) {
                if (listener != null) listener.onSuccess(finalResult);
            } else {
                if (errorMsg == null) errorMsg = "轮询超时，未获取结果";
                if (listener != null) listener.onFailure(errorMsg);
            }
        });
    }

    // ==================== 辅助方法：处理Opus帧格式 ====================
    /**
     * 将Opus帧列表包装成服务端要求的格式（每帧前加2字节小端序帧长）
     * 对应Python的 struct.pack('<H', frame_length) + opus_data
     * @param opusFrames Opus帧列表（每个元素为单个Opus帧的二进制数据）
     * @return 完整的Opus数据（可直接发送给服务端）
     */
    public static byte[] wrapOpusFrames(List<byte[]> opusFrames) {
        if (opusFrames == null || opusFrames.isEmpty()) {
            return new byte[0];
        }

        // 计算总长度（所有帧长度 + 每帧2字节前缀）
        int totalLen = 0;
        for (byte[] frame : opusFrames) {
            totalLen += frame.length + 2;
        }

        // 拼接帧前缀和帧数据
        byte[] result = new byte[totalLen];
        int offset = 0;
        for (byte[] frame : opusFrames) {
            int frameLen = frame.length;
            // 1. 写入2字节小端序帧长（低8位→高8位）
            result[offset++] = (byte) (frameLen & 0xFF);          // 低8位
            result[offset++] = (byte) ((frameLen >> 8) & 0xFF);  // 高8位
            // 2. 写入Opus帧数据
            System.arraycopy(frame, 0, result, offset, frameLen);
            offset += frameLen;
        }

        return result;
    }

    // ==================== 资源释放 ====================
    /**
     * 释放线程池资源（页面销毁时调用）
     */
    public void release() {
        if (executorService != null && !executorService.isShutdown()) {
            executorService.shutdown();
            try {
                // 等待线程池关闭（最多5秒）
                if (!executorService.awaitTermination(5, TimeUnit.SECONDS)) {
                    executorService.shutdownNow(); // 强制关闭
                }
            } catch (InterruptedException e) {
                executorService.shutdownNow();
                Thread.currentThread().interrupt();
            }
        }
    }

    // ==================== 内部类：JSON响应模型（与Python返回格式对齐） ====================
    /**
     * /upload-opus 接口响应模型
     * Python返回格式：{"code":0,"msg":"success","data":{"task_id":"xxx"}}
     */
    private static class UploadResponse {
        @SerializedName("code")
        int code;

        @SerializedName("msg")
        String msg;

        @SerializedName("data")
        UploadData data;

        private static class UploadData {
            @SerializedName("task_id") // 对应Python的task_id字段
            String taskId;
        }
    }

    /**
     * /get-result/{taskId} 接口响应模型
     * Python返回格式：{"code":0,"data":{"status":"done","asr_result":"xxx"}}
     */
    private static class ResultResponse {
        @SerializedName("code")
        int code;

        @SerializedName("msg")
        String msg;

        @SerializedName("data")
        ResultData data;

        private static class ResultData {
            @SerializedName("status")
            String status; // pending/processing/done/failed

            @SerializedName("asr_result") // 对应Python的asr_result字段
            String asrResult;
        }
    }
}
