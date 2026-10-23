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

import java.util.Locale;

public final class VoiceInputService extends InputMethodService implements DictationJob.Listener {
    private final Handler main = new Handler(Looper.getMainLooper());
    private DictationJob job;
    private TextView status;
    private ProgressBar progressBar;
    private LinearLayout pendingPanel;
    private Button pendingAction;
    private Button discardButton;
    private Button microphone;
    private Button voiceTab;
    private Button editTab;
    private Button pasteButton;
    private LinearLayout voicePanel;
    private LinearLayout editPanel;
    private String oneTimeCopy;
    private PcmRecorder recorder;
    private boolean finishingRecording;
    private int recordingSession;
    private int inputSession;
    private boolean inputStarted;
    private boolean confirmingDiscard;
    private boolean privateField;
    private boolean deleting;
    private boolean suppressDeleteClick;
    private final Runnable progressTick = new Runnable() {
        @Override public void run() {
            if (!job.running) return;
            renderProgress();
            main.postDelayed(this, 1000);
        }
    };
    private final Runnable cancelDiscardConfirmation = () -> {
        confirmingDiscard = false;
        updateUi();
    };
    private final Runnable repeatDelete = new Runnable() {
        @Override public void run() {
            if (!deleting) return;
            deleteOnce();
            main.postDelayed(this, 65);
        }
    };

    @Override public void onCreate() {
        super.onCreate();
        job = DictationJob.get(this);
        job.setListener(this);
        inputSession = job.newInputSession();
    }

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
        pendingPanel = new LinearLayout(this);
        pendingPanel.setGravity(Gravity.CENTER_VERTICAL);
        body.addView(pendingPanel);
        pendingAction = key(pendingPanel, "", 2, this::pendingActionClicked);
        discardButton = key(pendingPanel, "", 1, this::discardClicked);
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
        refreshStatus();
        return body;
    }

    @Override public void onStartInput(EditorInfo info, boolean restarting) {
        super.onStartInput(info, restarting);
        // A restart keeps the same text field; anything else is a new place to type into.
        if (!restarting || !inputStarted) inputSession = job.newInputSession();
        inputStarted = true;
        privateField = isPrivateField(info.inputType);
        refreshStatus();
    }

    @Override public void onStartInputView(EditorInfo info, boolean restarting) {
        super.onStartInputView(info, restarting);
        refreshStatus();
    }

    @Override public void onFinishInput() {
        // Leaving the text field ends the recording but keeps it: it is still transcribed.
        stopRecording();
        stopDeleting();
        inputStarted = false;
        inputSession = job.newInputSession();
        super.onFinishInput();
    }

    @Override public void onFinishInputView(boolean finishingInput) {
        // The keyboard hides when the screen turns off or the notification shade opens.
        // Stop recording and keep transcribing in the background instead of discarding.
        stopRecording();
        stopDeleting();
        super.onFinishInputView(finishingInput);
    }

    @Override public boolean onEvaluateFullscreenMode() { return false; }

    private void toggleRecording() {
        if (recorder != null) {
            stopRecording();
            return;
        }
        if (finishingRecording || job.running || privateField) return;
        if (job.hasUnfinished()) {
            refreshStatus();
            return;
        }
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
            PcmRecorder[] next = new PcmRecorder[1];
            next[0] = new PcmRecorder((pcm, error) -> onRecorded(next[0], pcm, error));
            next[0].start();
            recorder = next[0];
            recordingSession = inputSession;
            confirmingDiscard = false;
            show("正在录音 · 再点一次结束");
            updateUi();
        } catch (Exception error) {
            show("录音无法开始：" + error.getMessage());
        }
    }

    private void stopRecording() {
        if (recorder == null) return;
        PcmRecorder finished = recorder;
        recorder = null;
        finishingRecording = true;
        show("正在结束录音");
        updateUi();
        finished.finish();
    }

    private void onRecorded(PcmRecorder source, byte[] pcm, String error) {
        if (recorder == source) recorder = null;
        finishingRecording = false;
        if (error != null || pcm.length == 0) {
            show(error == null ? "没有录到声音" : error);
            updateUi();
            return;
        }
        job.submit(pcm, recordingSession);
    }

    @Override public void onDictationChanged() {
        if (!job.running && job.pendingText != null && job.targetSession == inputSession) {
            insertPending(true);
        }
        refreshStatus();
    }

    private void pendingActionClicked() {
        if (job.running) return;
        if (job.pendingText != null) insertPending(false);
        else if (job.audioKept) {
            // A retry types into whichever field is open now.
            job.submit(null, inputSession);
        }
    }

    private void insertPending(boolean automatic) {
        InputConnection target = getCurrentInputConnection();
        if (!inputStarted || target == null || privateField) {
            if (!automatic) show(privateField ? "密码输入框不能写入转写文字" : "请先点一个输入框");
            return;
        }
        if (!target.commitText(job.pendingText, 1)) {
            if (!automatic) show("当前输入框无法写入，文字仍保留");
            return;
        }
        String elapsed = seconds(job.finishedMillis);
        job.delivered();
        show(automatic ? "已输入文字 · 耗时 " + elapsed + " · 点击麦克风可继续"
                : "已插入转写文字 · 点击麦克风可继续");
        updateUi();
    }

    private void discardClicked() {
        if (recorder != null) {
            recorder.discard();
            recorder = null;
            show("已放弃本段录音");
            updateUi();
            return;
        }
        if (!confirmingDiscard) {
            confirmingDiscard = true;
            main.removeCallbacks(cancelDiscardConfirmation);
            main.postDelayed(cancelDiscardConfirmation, 3000);
            updateUi();
            return;
        }
        confirmingDiscard = false;
        main.removeCallbacks(cancelDiscardConfirmation);
        job.discard();
        show("已丢弃这段录音");
        updateUi();
    }

    /** Shows the job state; transient messages from other buttons stay until the next change. */
    private void refreshStatus() {
        if (recorder != null) show("正在录音 · 再点一次结束");
        else if (finishingRecording) show("正在结束录音");
        else if (job.running) {
            main.removeCallbacks(progressTick);
            main.postDelayed(progressTick, 1000);
        } else if (job.pendingText != null) {
            show("转写完成，尚未输入（" + job.pendingText.length() + " 字）：" + preview(job.pendingText)
                    + " · 点「插入文字」写入当前输入框");
        } else if (job.audioKept) {
            show((job.error == null ? "上次录音还没有转写完成" : "转写失败：" + job.error)
                    + " · 录音已保存在手机上，可点「重试」");
        } else if (job.error != null) show("转写失败：" + job.error);
        else if (privateField) show("密码输入框已禁用语音");
        else show("点击麦克风开始录音");
        updateUi();
    }

    private static String preview(String text) {
        String flat = text.replaceAll("\\s+", " ").trim();
        return "「" + (flat.length() > 24 ? flat.substring(0, 24) + "…" : flat) + "」";
    }

    private static String seconds(long millis) {
        return String.format(Locale.CHINA, "%.1f 秒", millis / 1000.0);
    }

    private void renderProgress() {
        if (!job.running || progressBar == null) return;
        progressBar.setVisibility(View.VISIBLE);
        progressBar.setIndeterminate(job.percent < 0);
        if (job.percent >= 0) progressBar.setProgress(job.percent);
        show(job.stage + (job.percent < 0 ? "" : " " + job.percent + "%")
                + " · 已等待 " + seconds(SystemClock.elapsedRealtime() - job.started)
                + " · 可锁屏或离开");
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
        microphone.setEnabled(recorder != null ||
                !finishingRecording && !job.hasUnfinished() && !privateField);
        microphone.setText(recorder == null ? "🎙 录音" : "■ 结束");
        if (pasteButton != null) pasteButton.setEnabled(oneTimeCopy != null);
        boolean saved = !job.running && (job.pendingText != null || job.audioKept);
        pendingPanel.setVisibility(recorder != null || job.running || saved ? View.VISIBLE : View.GONE);
        pendingAction.setVisibility(saved && recorder == null ? View.VISIBLE : View.GONE);
        pendingAction.setText(job.pendingText != null ? "插入文字" : "↻ 重试");
        pendingAction.setEnabled(job.pendingText == null || !privateField);
        if (recorder != null) discardButton.setText("× 放弃本段");
        else discardButton.setText(confirmingDiscard ? "再点确认丢弃" : job.running ? "× 取消" : "丢弃");
        if (job.running) renderProgress();
        else progressBar.setVisibility(View.GONE);
    }
    private int dp(int value) { return (int) (value * getResources().getDisplayMetrics().density + .5f); }

    @Override public void onDestroy() {
        // A running transcription continues in DictationJob and is shown next time.
        stopRecording();
        stopDeleting();
        main.removeCallbacksAndMessages(null);
        job.clearListener(this);
        oneTimeCopy = null;
        super.onDestroy();
    }
}
