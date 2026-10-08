package com.geminidictation.keyboard;

import android.net.Uri;
import android.os.SystemClock;
import android.util.Base64;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;
import okio.ByteString;

final class LiveTranscriber {
    interface ProgressListener {
        // Percentage measures audio submitted to the socket, not Gemini's processing progress.
        void onProgress(String stage, int percent);
    }

    private static final String ENDPOINT =
            "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent";
    private static final String MODEL = "models/gemini-3.5-transcribe-live";
    private static final OkHttpClient CLIENT = new OkHttpClient.Builder().readTimeout(0, TimeUnit.MILLISECONDS).build();

    private LiveTranscriber() { }

    static String transcribe(byte[] pcm, String apiKey, String mode, List<String> vocabulary,
                             ProgressListener progress) throws Exception {
        if (pcm.length == 0) throw new IllegalArgumentException("没有录到声音");
        double seconds = pcm.length / (PcmRecorder.SAMPLE_RATE * 2.0);
        if (seconds > PcmRecorder.MAX_SECONDS + 2) throw new IllegalArgumentException("录音太长，请分段录制");
        progress.onProgress("正在连接 Gemini", -1);

        CountDownLatch setup = new CountDownLatch(1);
        CountDownLatch done = new CountDownLatch(1);
        AtomicReference<String> failure = new AtomicReference<>();
        StringBuilder transcript = new StringBuilder();
        JSONObject setupMessage = new JSONObject().put("setup", new JSONObject()
                .put("model", MODEL)
                .put("generationConfig", new JSONObject().put("responseModalities", new JSONArray().put("TEXT")))
                .put("inputAudioTranscription", new JSONObject()
                        .put("languageCodes", new JSONArray())
                        .put("customVocabulary", new JSONArray(vocabulary))
                        .put("mode", mode))
                .put("realtimeInputConfig", new JSONObject()
                        .put("automaticActivityDetection", new JSONObject().put("disabled", true))));
        Request request = new Request.Builder().url(ENDPOINT + "?key=" + Uri.encode(apiKey)).build();
        WebSocket socket = CLIENT.newWebSocket(request, new WebSocketListener() {
            @Override public void onOpen(WebSocket webSocket, Response response) {
                webSocket.send(setupMessage.toString());
            }

            @Override public void onMessage(WebSocket webSocket, String text) {
                readMessage(text);
            }

            @Override public void onMessage(WebSocket webSocket, ByteString bytes) {
                readMessage(bytes.utf8());
            }

            private void readMessage(String text) {
                try {
                    JSONObject message = new JSONObject(text);
                    if (message.has("setupComplete")) setup.countDown();
                    JSONObject content = message.optJSONObject("serverContent");
                    if (content == null) return;
                    JSONObject transcription = content.optJSONObject("inputTranscription");
                    String part = transcription == null ? "" : transcription.optString("text", "").trim();
                    if (!part.isEmpty()) {
                        synchronized (transcript) {
                            if (transcript.length() > 0 && part.matches("^[A-Za-z0-9].*") &&
                                    transcript.toString().matches(".*[A-Za-z0-9.!?,:;]$")) transcript.append(' ');
                            transcript.append(part);
                        }
                    }
                    if (content.optBoolean("generationComplete")) done.countDown();
                } catch (Exception error) {
                    failure.compareAndSet(null, "Gemini 返回了无法解析的数据");
                    setup.countDown();
                    done.countDown();
                }
            }

            @Override public void onFailure(WebSocket webSocket, Throwable error, Response response) {
                failure.compareAndSet(null, error.getMessage() == null ? "Gemini 连接失败" :
                        error.getMessage().replace(apiKey, "[hidden]"));
                setup.countDown();
                done.countDown();
            }

            @Override public void onClosed(WebSocket webSocket, int code, String reason) {
                if (done.getCount() != 0) failure.compareAndSet(null, "Gemini 提前断开连接（" + code + "）");
                setup.countDown();
                done.countDown();
            }
        });
        try {
            if (!setup.await(20, TimeUnit.SECONDS)) throw new IllegalStateException("连接 Gemini 超时");
            if (failure.get() != null) throw new IllegalStateException(failure.get());
            send(socket, "{\"realtimeInput\":{\"activityStart\":{}}}");
            long started = SystemClock.elapsedRealtime();
            int lastPercent = -1;
            progress.onProgress("发送音频 · 已提交", 0);
            for (int offset = 0; offset < pcm.length; offset += 3200) {
                if (Thread.currentThread().isInterrupted()) throw new InterruptedException();
                if (failure.get() != null) throw new IllegalStateException(failure.get());
                double position = offset / (PcmRecorder.SAMPLE_RATE * 2.0);
                long sendAt = started + (long) (Math.max(0, position - 15) / 4 * 1000);
                long wait = sendAt - SystemClock.elapsedRealtime();
                if (wait > 0) Thread.sleep(wait);
                int length = Math.min(3200, pcm.length - offset);
                String encoded = Base64.encodeToString(pcm, offset, length, Base64.NO_WRAP);
                JSONObject chunk = new JSONObject().put("realtimeInput", new JSONObject()
                        .put("audio", new JSONObject().put("data", encoded)
                                .put("mimeType", "audio/pcm;rate=16000")));
                send(socket, chunk.toString());
                int percent = (int) ((offset + length) * 100L / pcm.length);
                if (percent != lastPercent) {
                    progress.onProgress("发送音频 · 已提交", percent);
                    lastPercent = percent;
                }
            }
            send(socket, "{\"realtimeInput\":{\"activityEnd\":{}}}");
            long timeout = (long) ((30 + seconds / 4 * 2) * 1000);
            long deadline = SystemClock.elapsedRealtime() + timeout;
            progress.onProgress("正在上传剩余音频", -1);
            // send() queues messages locally. Only label the next stage as waiting for
            // Gemini after OkHttp has written that queue to the network.
            boolean waitingForGemini = false;
            while (done.getCount() != 0) {
                if (!waitingForGemini && socket.queueSize() == 0) {
                    waitingForGemini = true;
                    progress.onProgress("等待 Gemini 完成转写", -1);
                }
                long remaining = deadline - SystemClock.elapsedRealtime();
                if (remaining <= 0) break;
                if (done.await(Math.min(remaining, 250), TimeUnit.MILLISECONDS)) break;
            }
            synchronized (transcript) {
                String result = transcript.toString().trim();
                if (!result.isEmpty()) return result;
            }
            if (failure.get() != null) throw new IllegalStateException(failure.get());
            throw new IllegalStateException("Gemini 没有返回文字，请重试");
        } finally {
            socket.cancel();
        }
    }

    private static void send(WebSocket socket, String message) {
        if (!socket.send(message)) throw new IllegalStateException("Gemini 连接已关闭");
    }
}
