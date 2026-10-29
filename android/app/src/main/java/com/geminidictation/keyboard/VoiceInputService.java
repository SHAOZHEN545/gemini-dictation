package com.geminidictation.keyboard;

import android.Manifest;
import android.content.ClipData;
import android.content.ClipDescription;
import android.content.ClipboardManager;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.RippleDrawable;
import android.inputmethodservice.InputMethodService;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.text.InputType;
import android.text.TextUtils;
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
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class VoiceInputService extends InputMethodService implements DictationJob.Listener {
    private static final int PANEL_VOICE = 0;
    private static final int PANEL_EDIT = 1;
    private static final int PANEL_CLIPBOARD = 2;
    // Shared by keyboard service instances in this process; used on the main thread.
    private static ClipboardHistory clipboardHistory;
    /** Last system clipboard text already considered, so deleted entries do not come back. */
    private static String lastSystemClip;
    private static final ExecutorService CLIPBOARD_SAVER = Executors.newSingleThreadExecutor();
    private final Handler main = new Handler(Looper.getMainLooper());
    private ClipboardManager clipboard;
    private final ClipboardManager.OnPrimaryClipChangedListener clipListener = this::recordSystemClip;
    private DictationJob job;
    private TextView status;
    private ProgressBar progressBar;
    private LinearLayout pendingPanel;
    private Button pendingAction;
    private Button discardButton;
    private Button microphone;
    private Button voiceTab;
    private Button editTab;
    private Button clipboardTab;
    private Button pasteButton;
    private LinearLayout voicePanel;
    private LinearLayout editPanel;
    private LinearLayout clipboardPanel;
    private LinearLayout clipboardList;
    private TextView clipboardTitle;
    private Button clearClipboardButton;
    private boolean confirmingClear;
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
    private final Runnable cancelClearConfirmation = () -> {
        confirmingClear = false;
        renderClipboard();
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
        if (clipboardHistory == null) clipboardHistory = ClipboardHistory.deserialize(readClipboardFile());
        clipboard = getSystemService(ClipboardManager.class);
        clipboard.addPrimaryClipChangedListener(clipListener);
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
        voiceTab = key(tabs, "🎙 语音", 2, () -> showPanel(PANEL_VOICE));
        editTab = key(tabs, "✎ 编辑", 2, () -> showPanel(PANEL_EDIT));
        clipboardTab = key(tabs, "📋 剪贴板", 2, () -> showPanel(PANEL_CLIPBOARD));
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
        pasteButton = key(actions, "粘贴", 1, this::pasteLatest);

        clipboardPanel = new LinearLayout(this);
        clipboardPanel.setOrientation(LinearLayout.VERTICAL);
        body.addView(clipboardPanel);
        LinearLayout clipboardHeader = new LinearLayout(this);
        clipboardHeader.setGravity(Gravity.CENTER_VERTICAL);
        clipboardPanel.addView(clipboardHeader);
        clipboardTitle = new TextView(this);
        clipboardTitle.setTextColor(Color.rgb(200, 210, 228));
        clipboardTitle.setTextSize(13);
        clipboardHeader.addView(clipboardTitle, new LinearLayout.LayoutParams(
                0, LinearLayout.LayoutParams.WRAP_CONTENT, 3));
        clearClipboardButton = key(clipboardHeader, "清空", 1, this::clearClipboardClicked);
        ScrollView clipboardScroll = new ScrollView(this);
        clipboardScroll.setScrollbarFadingEnabled(false);
        clipboardList = new LinearLayout(this);
        clipboardList.setOrientation(LinearLayout.VERTICAL);
        clipboardScroll.addView(clipboardList);
        clipboardPanel.addView(clipboardScroll, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(190)));
        showPanel(PANEL_VOICE);
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
        // Picks up a copy made while another keyboard was active.
        recordSystemClip();
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
        // Keep a copy in the clipboard history in case the text field loses it later.
        addToClipboardHistory(job.pendingText);
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

    private void showPanel(int panel) {
        voicePanel.setVisibility(panel == PANEL_VOICE ? View.VISIBLE : View.GONE);
        editPanel.setVisibility(panel == PANEL_EDIT ? View.VISIBLE : View.GONE);
        clipboardPanel.setVisibility(panel == PANEL_CLIPBOARD ? View.VISIBLE : View.GONE);
        voiceTab.setEnabled(panel != PANEL_VOICE);
        editTab.setEnabled(panel != PANEL_EDIT);
        clipboardTab.setEnabled(panel != PANEL_CLIPBOARD);
        confirmingClear = false;
        renderClipboard();
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
        String text = selected.toString();
        lastSystemClip = text;
        addToClipboardHistory(text);
        try {
            clipboard.setPrimaryClip(ClipData.newPlainText("文字", text));
        } catch (RuntimeException ignored) {
            // Still kept in the keyboard's clipboard history.
        }
        show("已复制，可在「剪贴板」里再次粘贴");
    }

    private void pasteLatest() {
        List<String> items = clipboardHistory.items();
        if (!items.isEmpty()) pasteClip(items.get(0));
    }

    private void pasteClip(String text) {
        InputConnection target = getCurrentInputConnection();
        if (target == null) show("请先点一个输入框");
        else if (target.commitText(text, 1)) show("已粘贴");
        else show("当前输入框无法粘贴");
    }

    private void recordSystemClip() {
        try {
            ClipData clip = clipboard.getPrimaryClip();
            if (clip == null || clip.getItemCount() == 0) return;
            ClipDescription description = clip.getDescription();
            // Password managers mark copied secrets as sensitive; never keep those.
            if (description != null && description.getExtras() != null &&
                    description.getExtras().getBoolean("android.content.extra.IS_SENSITIVE")) return;
            CharSequence text = clip.getItemAt(0).getText();
            if (text == null || text.toString().equals(lastSystemClip)) return;
            lastSystemClip = text.toString();
            addToClipboardHistory(text);
        } catch (RuntimeException ignored) {
            // The clipboard may be unavailable to the keyboard; history stays as it is.
        }
    }

    private void addToClipboardHistory(CharSequence text) {
        if (!clipboardHistory.add(text)) return;
        saveClipboardHistory();
        renderClipboard();
        updateUi();
    }

    private void deleteClip(String text) {
        int index = clipboardHistory.items().indexOf(text);
        if (index < 0) return;
        clipboardHistory.remove(index);
        forgetSystemClip(text);
        saveClipboardHistory();
        show("已删除一条剪贴板记录");
        renderClipboard();
        updateUi();
    }

    private void clearClipboardClicked() {
        if (!confirmingClear) {
            confirmingClear = true;
            main.removeCallbacks(cancelClearConfirmation);
            main.postDelayed(cancelClearConfirmation, 3000);
            renderClipboard();
            return;
        }
        confirmingClear = false;
        main.removeCallbacks(cancelClearConfirmation);
        for (String item : clipboardHistory.items()) forgetSystemClip(item);
        clipboardHistory.clear();
        saveClipboardHistory();
        show("剪贴板已清空");
        renderClipboard();
        updateUi();
    }

    /** Also clears the system clipboard when it still holds a deleted text. */
    private void forgetSystemClip(String text) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) return;
        try {
            ClipData clip = clipboard.getPrimaryClip();
            CharSequence current = clip == null || clip.getItemCount() == 0 ? null : clip.getItemAt(0).getText();
            if (current != null && current.toString().equals(text)) clipboard.clearPrimaryClip();
        } catch (RuntimeException ignored) {
            // Nothing else to clean up.
        }
    }

    private void renderClipboard() {
        if (clipboardList == null) return;
        List<String> items = clipboardHistory.items();
        clipboardTitle.setText("最近复制 " + items.size() + "/" + ClipboardHistory.LIMIT + " · 点一下粘贴，长按删除");
        clearClipboardButton.setText(confirmingClear ? "再点确认清空" : "清空");
        clearClipboardButton.setEnabled(!items.isEmpty());
        clipboardList.removeAllViews();
        if (items.isEmpty()) {
            TextView empty = new TextView(this);
            empty.setTextColor(Color.rgb(200, 210, 228));
            empty.setTextSize(14);
            empty.setPadding(dp(4), dp(10), dp(4), dp(10));
            empty.setText("还没有复制过文字。在任意 App 里复制后会出现在这里，最多保留最近 "
                    + ClipboardHistory.LIMIT + " 条。");
            clipboardList.addView(empty);
            return;
        }
        for (String item : items) {
            TextView row = new TextView(this);
            row.setText(item.replaceAll("\\s+", " ").trim());
            row.setMaxLines(2);
            row.setEllipsize(TextUtils.TruncateAt.END);
            row.setTextColor(Color.WHITE);
            row.setTextSize(15);
            row.setPadding(dp(10), dp(8), dp(10), dp(8));
            row.setBackground(new RippleDrawable(ColorStateList.valueOf(Color.rgb(86, 110, 150)),
                    new ColorDrawable(Color.rgb(40, 56, 84)), null));
            row.setOnClickListener(view -> pasteClip(item));
            row.setOnLongClickListener(view -> {
                deleteClip(item);
                return true;
            });
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
            params.bottomMargin = dp(4);
            clipboardList.addView(row, params);
        }
    }

    private File clipboardFile() { return new File(getNoBackupFilesDir(), "clipboard-history.bin"); }

    private byte[] readClipboardFile() {
        File file = clipboardFile();
        if (!file.isFile()) return null;
        try {
            return Files.readAllBytes(file.toPath());
        } catch (IOException error) {
            return null;
        }
    }

    private void saveClipboardHistory() {
        byte[] data = clipboardHistory.serialize();
        File file = clipboardFile();
        CLIPBOARD_SAVER.execute(() -> {
            try {
                DictationStore.write(file, data);
            } catch (IOException ignored) {
                // The history is still in memory and is saved again on the next change.
            }
        });
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
        if (pasteButton != null) pasteButton.setEnabled(!clipboardHistory.items().isEmpty());
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
        clipboard.removePrimaryClipChangedListener(clipListener);
        super.onDestroy();
    }
}
