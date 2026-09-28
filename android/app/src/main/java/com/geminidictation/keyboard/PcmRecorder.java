package com.geminidictation.keyboard;

import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.Handler;
import android.os.Looper;

import java.io.ByteArrayOutputStream;
import java.util.concurrent.atomic.AtomicBoolean;

final class PcmRecorder {
    interface Callback {
        void finished(byte[] pcm, String error);
    }

    static final int SAMPLE_RATE = 16_000;
    static final int MAX_SECONDS = 510;
    private final AudioRecord recorder;
    private final Callback callback;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final AtomicBoolean recording = new AtomicBoolean(false);
    private volatile boolean discard;
    private Thread thread;

    PcmRecorder(Callback callback) {
        this.callback = callback;
        int minBytes = AudioRecord.getMinBufferSize(SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        if (minBytes <= 0) throw new IllegalStateException("手机不支持 16 kHz 麦克风录音");
        recorder = new AudioRecord(MediaRecorder.AudioSource.MIC, SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
                Math.max(minBytes, 6400));
        if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
            recorder.release();
            throw new IllegalStateException("麦克风无法打开");
        }
    }

    void start() {
        try {
            recorder.startRecording();
        } catch (RuntimeException error) {
            recorder.release();
            throw error;
        }
        if (recorder.getRecordingState() != AudioRecord.RECORDSTATE_RECORDING) {
            recorder.release();
            throw new IllegalStateException("麦克风没有开始录音");
        }
        recording.set(true);
        thread = new Thread(() -> {
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            byte[] chunk = new byte[3200];
            String error = null;
            try {
                while (recording.get() && output.size() < SAMPLE_RATE * 2 * MAX_SECONDS) {
                    int count = recorder.read(chunk, 0, chunk.length);
                    if (count < 0) {
                        if (!recording.get()) break;
                        throw new IllegalStateException("麦克风读取失败：" + count);
                    }
                    output.write(chunk, 0, count);
                }
            } catch (Exception failure) {
                if (!discard) error = failure.getMessage();
            } finally {
                recording.set(false);
                try { recorder.stop(); } catch (IllegalStateException ignored) { }
                recorder.release();
                if (!discard) {
                    byte[] pcm = output.toByteArray();
                    String resultError = error;
                    main.post(() -> callback.finished(pcm, resultError));
                }
            }
        }, "dictation-audio");
        thread.start();
    }

    void finish() {
        recording.set(false);
        try { recorder.stop(); } catch (IllegalStateException ignored) { }
    }

    void discard() {
        discard = true;
        finish();
    }
}
