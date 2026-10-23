package com.geminidictation.keyboard;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;

import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

/**
 * Process-wide transcription state. It outlives the keyboard window and the keyboard service,
 * so turning off the screen, pulling down the notification shade, or switching apps does not
 * cancel an upload. All fields and methods are used on the main thread.
 */
final class DictationJob {
    interface Listener {
        void onDictationChanged();
    }

    private static DictationJob instance;

    private final Context app;
    private final DictationStore store;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final Handler main = new Handler(Looper.getMainLooper());
    private final PowerManager.WakeLock wakeLock;
    private Listener listener;
    private Future<?> task;
    private int attempt;
    private int nextInputSession;

    boolean running;
    String stage;
    int percent = -1;
    long started;
    long finishedMillis;
    /** Finished transcript that has not been typed into a text field yet. */
    String pendingText;
    /** A recording is saved on the phone and can be sent again. */
    boolean audioKept;
    String error;
    /** Input session the recording was made in; the result is typed there automatically. */
    int targetSession = -1;

    static DictationJob get(Context context) {
        if (instance == null) instance = new DictationJob(context.getApplicationContext());
        return instance;
    }

    private DictationJob(Context app) {
        this.app = app;
        store = new DictationStore(app.getNoBackupFilesDir());
        pendingText = store.readText();
        audioKept = store.hasAudio();
        wakeLock = ((PowerManager) app.getSystemService(Context.POWER_SERVICE))
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "GeminiDictation:transcribe");
        wakeLock.setReferenceCounted(false);
    }

    static boolean isRunning() { return instance != null && instance.running; }

    int newInputSession() { return ++nextInputSession; }

    void setListener(Listener next) { listener = next; }

    void clearListener(Listener old) { if (listener == old) listener = null; }

    boolean hasUnfinished() { return running || pendingText != null || audioKept; }

    /** Transcribes a new recording, or the saved one when {@code pcm} is null. */
    void submit(byte[] pcm, int session) {
        if (running) return;
        int id = ++attempt;
        running = true;
        stage = pcm == null ? "正在读取已保存的录音" : "正在保存录音";
        percent = -1;
        started = SystemClock.elapsedRealtime();
        error = null;
        pendingText = null;
        targetSession = session;
        keepAlive(true);
        task = worker.submit(() -> run(id, pcm));
        changed();
    }

    /** Called after the transcript has been typed into a text field. */
    void delivered() {
        pendingText = null;
        audioKept = false;
        error = null;
        worker.execute(store::clear);
        changed();
    }

    /** Cancels any upload and deletes the saved recording and transcript. */
    void discard() {
        attempt++;
        if (task != null) task.cancel(true);
        task = null;
        running = false;
        pendingText = null;
        audioKept = false;
        error = null;
        keepAlive(false);
        // Queued after the cancelled task, so it cannot recreate the files afterwards.
        worker.execute(store::clear);
        changed();
    }

    private void run(int id, byte[] recorded) {
        String key = "";
        try {
            byte[] pcm = recorded;
            if (pcm == null) pcm = store.readAudio();
            else {
                try {
                    store.saveAudio(pcm);
                    post(id, () -> audioKept = true);
                } catch (Exception ignored) {
                    // Saving is only a safety net; still try to transcribe.
                }
            }
            post(id, () -> { stage = "正在读取词库"; percent = -1; });
            key = SecureKeyStore.read(app);
            if (key.isEmpty()) throw new IllegalStateException("Gemini Key 未设置");
            List<String> vocabulary = VocabularyStore.parse(VocabularyStore.read(app));
            String text = LiveTranscriber.transcribe(pcm, key, "VERBATIM", vocabulary,
                    (nextStage, nextPercent) -> post(id, () -> { stage = nextStage; percent = nextPercent; }));
            try {
                store.saveText(text);
            } catch (Exception ignored) {
                // The transcript is still kept in memory.
            }
            post(id, () -> {
                running = false;
                finishedMillis = SystemClock.elapsedRealtime() - started;
                pendingText = text;
                keepAlive(false);
            });
        } catch (InterruptedException cancelled) {
            Thread.currentThread().interrupt();
        } catch (Exception failure) {
            if (Thread.currentThread().isInterrupted()) return;
            String safe = failure.getMessage() == null ? "转写失败" : failure.getMessage();
            if (!key.isEmpty()) safe = safe.replace(key, "[hidden]");
            String message = safe;
            post(id, () -> {
                running = false;
                error = message;
                audioKept = store.hasAudio();
                keepAlive(false);
            });
        }
    }

    private void post(int id, Runnable update) {
        main.post(() -> {
            if (id != attempt) return;
            update.run();
            changed();
        });
    }

    private void changed() {
        if (listener != null) listener.onDictationChanged();
    }

    private void keepAlive(boolean on) {
        if (on) {
            // Released when the job ends; the timeout only guards against a stuck job.
            wakeLock.acquire(20 * 60 * 1000L);
            TranscriptionService.start(app);
        } else {
            if (wakeLock.isHeld()) wakeLock.release();
            TranscriptionService.stopIfIdle();
        }
    }
}
