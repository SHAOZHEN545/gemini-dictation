package com.geminidictation.keyboard;

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.inputmethodservice.InputMethodService;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.text.InputType;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.ExtractedText;
import android.view.inputmethod.ExtractedTextRequest;
import android.view.inputmethod.InputConnection;
import android.view.inputmethod.InputMethodManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

public final class VoiceInputService extends InputMethodService {
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final Handler main = new Handler(Looper.getMainLooper());
    private TextView status;
    private ProgressBar progressBar;
    private String processingStage;
    private int processingPercent = -1;
    private long processingStarted;
    private Button microphone;
    private Button voiceTab;
    private Button editTab;
    private Button pasteButton;
    private LinearLayout voicePanel;
    private LinearLayout editPanel;
    private String oneTimeCopy;
    private PcmRecorder recorder;
    private Future<?> transcription;
    private int editorSession;
    private boolean busy;
    private boolean privateField;
    private boolean deleting;
    private boolean suppressDeleteClick;
    private final Runnable progressTick = new Runnable() {
        @Override public void run() {
            if (!busy) return;
            renderProgress();
            main.postDelayed(this, 1000);
        }
    };
    private final Runnable repeatDelete = new Runnable() {
        @Override public void run() {
            if (!deleting) return;
            deleteOnce();
            main.postDelayed(this, 65);
        }
    };

    @Override public View onCreateInputView() {
        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(dp(10), dp(9), dp(10), dp(9));
        body.setBackgroundColor(Color.rgb(23, 35, 56));
        status = new TextView(this);
        status.setTextColor(Color.WHITE);
        status.setTextSize(15);
        status.setText("点击麦克风开始录音");
        body.addView(status);
        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setMax(100);
        progressBar.setVisibility(View.GONE);
        body.addView(progressBar, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(6)));
        LinearLayout tabs = new LinearLayout(this);
        tabs.setGravity(Gravity.CENTER_VERTICAL);
        body.addView(tabs);
        voiceTab = key(tabs, "🎙 语音", 2, () -> showPanel(false));
        editTab = key(tabs, "✎ 编辑", 2, () -> showPanel(true));
        key(tabs, "🌐", 1, () -> {
            if (shouldOfferSwitchingToNextInputMethod()) switchToNextInputMethod(false);
            else ((InputMethodManager) getSystemService(INPUT_METHOD_SERVICE)).showInputMethodPicker();
        });
        key(tabs, "⚙", 1, this::openSettings);

        voicePanel = new LinearLayout(this);
        voicePanel.setGravity(Gravity.CENTER_VERTICAL);
        body.addView(voicePanel);
        LinearLayout row = voicePanel;
        microphone = key(row, "🎙 录音", 2, this::toggleRecording);
        Button delete = key(row, "⌫", 1, () -> {
            if (!suppressDeleteClick) deleteOnce();
        });
        delete.setOnTouchListener((view, event) -> {
            if (event.getActionMasked() == MotionEvent.ACTION_DOWN) {
                deleting = true;
                view.setPressed(true);
                deleteOnce();
                main.postDelayed(repeatDelete, 400);
                return true;
            }
            if (event.getActionMasked() == MotionEvent.ACTION_UP ||
                    event.getActionMasked() == MotionEvent.ACTION_CANCEL) {
                stopDeleting();
                view.setPressed(false);
                if (event.getActionMasked() == MotionEvent.ACTION_UP) {
                    suppressDeleteClick = true;
                    view.performClick(); // Keep the button accessible without deleting twice.
                    suppressDeleteClick = false;
                }
                return true;
            }
            return deleting;
        });
        key(row, "空格", 1, () -> commit(" "));
        key(row, "换行", 1, this::enter);

        editPanel = new LinearLayout(this);
        editPanel.setOrientation(LinearLayout.VERTICAL);
        body.addView(editPanel);
        LinearLayout arrows = new LinearLayout(this);
        editPanel.addView(arrows);
        key(arrows, "←", 1, () -> moveCursor(KeyEvent.KEYCODE_DPAD_LEFT));
        key(arrows, "↑", 1, () -> moveCursor(KeyEvent.KEYCODE_DPAD_UP));
        key(arrows, "↓", 1, () -> moveCursor(KeyEvent.KEYCODE_DPAD_DOWN));
        key(arrows, "→", 1, () -> moveCursor(KeyEvent.KEYCODE_DPAD_RIGHT));
        LinearLayout actions = new LinearLayout(this);
        editPanel.addView(actions);
        key(actions, "全选", 1, this::selectAll);
        key(actions, "复制", 1, this::copyOnce);
        pasteButton = key(actions, "粘贴一次", 1, this::pasteOnce);
        showPanel(false);
        show(privateField ? "密码输入框已禁用语音" : "点击麦克风开始录音");
        updateUi();
        return body;
    }

    @Override public void onStartInput(EditorInfo info, boolean restarting) {
        super.onStartInput(info, restarting);
        resetSession();
        privateField = isPrivateField(info.inputType);
        show(privateField ? "密码输入框已禁用语音" : "点击麦克风开始录音");
        updateUi();
    }

    @Override public void onFinishInput() {
        resetSession();
        super.onFinishInput();
    }

    @Override public void onFinishInputView(boolean finishingInput) {
        resetSession();
        super.onFinishInputView(finishingInput);
    }

    @Override public boolean onEvaluateFullscreenMode() { return false; }

    private void toggleRecording() {
        if (recorder != null) {
            PcmRecorder finished = recorder;
            recorder = null;
            startProcessing("正在结束录音");
            finished.finish();
            return;
        }
        if (busy || privateField) return;
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            show("请在设置中授权麦克风");
            openSettings();
            return;
        }
        try {
            if (SecureKeyStore.read(this).isEmpty()) {
                show("请先从 Bitwarden 粘贴并保存 Gemini Key");
                openSettings();
                return;
            }
            int capturedSession = editorSession;
            PcmRecorder next = new PcmRecorder((pcm, error) -> onRecorded(capturedSession, pcm, error));
            next.start();
            recorder = next;
            show("正在录音 · 再点一次结束");
            updateUi();
        } catch (Exception error) {
            show("录音无法开始：" + error.getMessage());
        }
    }

    private void onRecorded(int capturedSession, byte[] pcm, String error) {
        if (capturedSession != editorSession) return;
        recorder = null;
        if (error != null || pcm.length == 0) {
            finishProcessing();
            show(error == null ? "没有录到声音" : error);
            updateUi();
            return;
        }
        startProcessing("正在读取词库");
        transcription = worker.submit(() -> {
            String key = "";
            try {
                key = SecureKeyStore.read(this);
                if (key.isEmpty()) throw new IllegalStateException("Gemini Key 未设置");
                List<String> vocabulary = VocabularyStore.parse(VocabularyStore.read(this));
                String text = LiveTranscriber.transcribe(pcm, key, "VERBATIM", vocabulary,
                        (stage, percent) -> main.post(() -> {
                            if (capturedSession != editorSession || !busy) return;
                            processingStage = stage;
                            processingPercent = percent;
                            renderProgress();
                        }));
                main.post(() -> {
                    if (capturedSession != editorSession) return;
                    String elapsed = elapsedProcessing();
                    finishProcessing();
                    InputConnection target = getCurrentInputConnection();
                    if (target != null && !privateField) {
                        target.commitText(text, 1);
                        show("已输入文字 · 耗时 " + elapsed + " · 点击麦克风可继续");
                    } else show("当前输入框已关闭，文字未写入");
                    updateUi();
                });
            } catch (Exception failure) {
                String safe = failure.getMessage() == null ? "转写失败" : failure.getMessage();
                if (!key.isEmpty()) safe = safe.replace(key, "[hidden]");
                String displayError = safe;
                main.post(() -> {
                    if (capturedSession != editorSession) return;
                    finishProcessing();
                    show("转写失败：" + displayError);
                    updateUi();
                });
            }
        });
    }

    private void resetSession() {
        editorSession++;
        stopDeleting();
        if (recorder != null) recorder.discard();
        recorder = null;
        if (transcription != null) transcription.cancel(true);
        transcription = null;
        finishProcessing();
        updateUi();
    }

    private void startProcessing(String stage) {
        if (!busy) processingStarted = SystemClock.elapsedRealtime();
        busy = true;
        processingStage = stage;
        processingPercent = -1;
        main.removeCallbacks(progressTick);
        renderProgress();
        main.postDelayed(progressTick, 1000);
        updateUi();
    }

    private void finishProcessing() {
        busy = false;
        main.removeCallbacks(progressTick);
        if (progressBar != null) progressBar.setVisibility(View.GONE);
    }

    private String elapsedProcessing() {
        return String.format(Locale.CHINA, "%.1f 秒",
                (SystemClock.elapsedRealtime() - processingStarted) / 1000.0);
    }

    private void renderProgress() {
        if (!busy || progressBar == null) return;
        progressBar.setVisibility(View.VISIBLE);
        progressBar.setIndeterminate(processingPercent < 0);
        if (processingPercent >= 0) progressBar.setProgress(processingPercent);
        show(processingStage + (processingPercent < 0 ? "" : " " + processingPercent + "%")
                + " · 已等待 " + elapsedProcessing());
    }

    private boolean isPrivateField(int inputType) {
        int variation = inputType & InputType.TYPE_MASK_VARIATION;
        int type = inputType & InputType.TYPE_MASK_CLASS;
        return type == InputType.TYPE_CLASS_TEXT &&
                (variation == InputType.TYPE_TEXT_VARIATION_PASSWORD ||
                        variation == InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD ||
                        variation == InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD) ||
                type == InputType.TYPE_CLASS_NUMBER && variation == InputType.TYPE_NUMBER_VARIATION_PASSWORD;
    }

    private void commit(String text) {
        InputConnection target = getCurrentInputConnection();
        if (target != null) target.commitText(text, 1);
    }

    private void enter() {
        InputConnection target = getCurrentInputConnection();
        if (target == null) return;
        target.commitText("\n", 1);
    }

    private void showPanel(boolean editing) {
        voicePanel.setVisibility(editing ? View.GONE : View.VISIBLE);
        editPanel.setVisibility(editing ? View.VISIBLE : View.GONE);
        voiceTab.setEnabled(editing);
        editTab.setEnabled(!editing);
        updateUi();
    }

    private void moveCursor(int keyCode) {
        if (getCurrentInputConnection() == null) return;
        sendDownUpKeyEvents(keyCode);
    }

    private void selectAll() {
        InputConnection target = getCurrentInputConnection();
        if (target == null) return;
        if (target.performContextMenuAction(android.R.id.selectAll)) return;
        ExtractedText extracted = target.getExtractedText(new ExtractedTextRequest(), 0);
        if (extracted != null && extracted.text != null && extracted.startOffset >= 0 &&
                target.setSelection(extracted.startOffset, extracted.startOffset + extracted.text.length())) return;
        show("当前输入框不支持全选");
    }

    private void copyOnce() {
        if (privateField) { show("密码输入框不能复制内容"); return; }
        InputConnection target = getCurrentInputConnection();
        if (target == null) return;
        CharSequence selected = target.getSelectedText(0);
        if (selected == null || selected.length() == 0) {
            show("请先选中文字；可点“全选”");
            return;
        }
        oneTimeCopy = selected.toString();
        show("已暂存选中文字，粘贴一次后清除");
        updateUi();
    }

    private void pasteOnce() {
        InputConnection target = getCurrentInputConnection();
        if (target == null || oneTimeCopy == null) return;
        if (target.commitText(oneTimeCopy, 1)) {
            oneTimeCopy = null;
            show("已粘贴，暂存内容已清除");
            updateUi();
        } else show("当前输入框无法粘贴，暂存内容仍在");
    }

    private void deleteOnce() {
        if (getCurrentInputConnection() != null) sendDownUpKeyEvents(KeyEvent.KEYCODE_DEL);
    }

    private void stopDeleting() {
        deleting = false;
        main.removeCallbacks(repeatDelete);
    }

    private void openSettings() {
        Intent intent = new Intent(this, SettingsActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivity(intent);
    }

    private Button key(LinearLayout row, String label, int weight, Runnable action) {
        Button button = new Button(this);
        button.setText(label);
        button.setTextSize(14);
        button.setAllCaps(false);
        button.setOnClickListener(view -> action.run());
        row.addView(button, new LinearLayout.LayoutParams(0, dp(62), weight));
        return button;
    }

    private void show(String text) { if (status != null) status.setText(text); }
    private void updateUi() {
        if (microphone == null) return;
        microphone.setEnabled(!busy && !privateField);
        microphone.setText(recorder == null ? "🎙 录音" : "■ 结束");
        if (pasteButton != null) pasteButton.setEnabled(oneTimeCopy != null);
        if (busy) renderProgress();
    }
    private int dp(int value) { return (int) (value * getResources().getDisplayMetrics().density + .5f); }

    @Override public void onDestroy() {
        resetSession();
        oneTimeCopy = null;
        worker.shutdownNow();
        super.onDestroy();
    }
}
