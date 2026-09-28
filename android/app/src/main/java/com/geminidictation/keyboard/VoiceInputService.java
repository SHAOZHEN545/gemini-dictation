package com.geminidictation.keyboard;

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.inputmethodservice.InputMethodService;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputConnection;
import android.view.inputmethod.InputMethodManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

public final class VoiceInputService extends InputMethodService {
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final Handler main = new Handler(Looper.getMainLooper());
    private TextView status;
    private Button microphone;
    private PcmRecorder recorder;
    private Future<?> transcription;
    private int editorSession;
    private boolean busy;
    private boolean privateField;

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
        LinearLayout row = new LinearLayout(this);
        row.setGravity(Gravity.CENTER_VERTICAL);
        body.addView(row);
        microphone = key(row, "🎙 录音", 2, this::toggleRecording);
        key(row, "⌫", 1, () -> {
            InputConnection target = getCurrentInputConnection();
            if (target != null) target.deleteSurroundingTextInCodePoints(1, 0);
        });
        key(row, "空格", 1, () -> commit(" "));
        key(row, "↵", 1, this::enter);
        key(row, "🌐", 1, () -> {
            if (shouldOfferSwitchingToNextInputMethod()) switchToNextInputMethod(false);
            else ((InputMethodManager) getSystemService(INPUT_METHOD_SERVICE)).showInputMethodPicker();
        });
        key(row, "⚙", 1, this::openSettings);
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
            busy = true;
            show("录音结束，准备转写…");
            updateUi();
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
            busy = false;
            show(error == null ? "没有录到声音" : error);
            updateUi();
            return;
        }
        busy = true;
        show("连接 Gemini 转写中…");
        updateUi();
        transcription = worker.submit(() -> {
            String key = "";
            try {
                key = SecureKeyStore.read(this);
                if (key.isEmpty()) throw new IllegalStateException("Gemini Key 未设置");
                List<String> vocabulary = VocabularyStore.parse(VocabularyStore.read(this));
                String text = LiveTranscriber.transcribe(pcm, key, "VERBATIM", vocabulary);
                main.post(() -> {
                    if (capturedSession != editorSession) return;
                    busy = false;
                    InputConnection target = getCurrentInputConnection();
                    if (target != null && !privateField) {
                        target.commitText(text, 1);
                        show("已输入文字 · 点击麦克风可继续");
                    } else show("当前输入框已关闭，文字未写入");
                    updateUi();
                });
            } catch (Exception failure) {
                String safe = failure.getMessage() == null ? "转写失败" : failure.getMessage();
                if (!key.isEmpty()) safe = safe.replace(key, "[hidden]");
                String displayError = safe;
                main.post(() -> {
                    if (capturedSession != editorSession) return;
                    busy = false;
                    show("转写失败：" + displayError);
                    updateUi();
                });
            }
        });
    }

    private void resetSession() {
        editorSession++;
        if (recorder != null) recorder.discard();
        recorder = null;
        if (transcription != null) transcription.cancel(true);
        transcription = null;
        busy = false;
        updateUi();
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
        EditorInfo info = getCurrentInputEditorInfo();
        if (target == null) return;
        int action = info == null ? EditorInfo.IME_ACTION_NONE : info.imeOptions & EditorInfo.IME_MASK_ACTION;
        if (action != EditorInfo.IME_ACTION_NONE && action != EditorInfo.IME_ACTION_UNSPECIFIED)
            target.performEditorAction(action);
        else target.commitText("\n", 1);
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
    }
    private int dp(int value) { return (int) (value * getResources().getDisplayMetrics().density + .5f); }

    @Override public void onDestroy() {
        resetSession();
        worker.shutdownNow();
        super.onDestroy();
    }
}
