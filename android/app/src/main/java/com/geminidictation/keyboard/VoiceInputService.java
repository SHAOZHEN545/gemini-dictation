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
        status.setText(R.string.status_idle);
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
        voiceTab = key(tabs, getString(R.string.tab_voice), 2, () -> showPanel(PANEL_VOICE));
        editTab = key(tabs, getString(R.string.tab_edit), 2, () -> showPanel(PANEL_EDIT));
        clipboardTab = key(tabs, getString(R.string.tab_clipboard), 2, () -> showPanel(PANEL_CLIPBOARD));
        key(tabs, "🌐", 1, () -> {
            if (shouldOfferSwitchingToNextInputMethod()) switchToNextInputMethod(false);
            else ((InputMethodManager) getSystemService(INPUT_METHOD_SERVICE)).showInputMethodPicker();
        });
        key(tabs, "⚙", 1, this::openSettings);

        voicePanel = new LinearLayout(this);
        voicePanel.setGravity(Gravity.CENTER_VERTICAL);
        body.addView(voicePanel);
        LinearLayout row = voicePanel;
        microphone = key(row, getString(R.string.key_record), 2, this::toggleRecording);
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
        key(row, getString(R.string.key_space), 1, () -> commit(" "));
        key(row, getString(R.string.key_enter), 1, this::enter);

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
        key(actions, getString(R.string.key_select_all), 1, this::selectAll);
        key(actions, getString(R.string.key_copy), 1, this::copyOnce);
        pasteButton = key(actions, getString(R.string.key_paste), 1, this::pasteLatest);

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
        clearClipboardButton = key(clipboardHeader, getString(R.string.key_clear), 1, this::clearClipboardClicked);
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
            show(getString(R.string.status_need_mic_permission));
            openSettings();
            return;
        }
        try {
            if (SecureKeyStore.read(this).isEmpty()) {
                show(getString(R.string.status_need_key));
                openSettings();
                return;
            }
            PcmRecorder[] next = new PcmRecorder[1];
            next[0] = new PcmRecorder(this, (pcm, error) -> onRecorded(next[0], pcm, error));
            next[0].start();
            recorder = next[0];
            recordingSession = inputSession;
            confirmingDiscard = false;
            show(getString(R.string.status_recording));
            updateUi();
        } catch (Exception error) {
            show(getString(R.string.status_record_failed, error.getMessage()));
        }
    }

    private void stopRecording() {
        if (recorder == null) return;
        PcmRecorder finished = recorder;
        recorder = null;
        finishingRecording = true;
        show(getString(R.string.status_finishing));
        updateUi();
        finished.finish();
    }

    private void onRecorded(PcmRecorder source, byte[] pcm, String error) {
        if (recorder == source) recorder = null;
        finishingRecording = false;
        if (error != null || pcm.length == 0) {
            show(error == null ? getString(R.string.error_no_sound) : error);
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
            if (!automatic)
                show(getString(privateField ? R.string.status_private_no_insert : R.string.status_tap_field));
            return;
        }
        if (!target.commitText(job.pendingText, 1)) {
            if (!automatic) show(getString(R.string.status_insert_failed));
            return;
        }
        String elapsed = seconds(job.finishedMillis);
        // Keep a copy in the clipboard history in case the text field loses it later.
        addToClipboardHistory(job.pendingText);
        job.delivered();
        show(automatic ? getString(R.string.status_typed, elapsed) : getString(R.string.status_inserted));
        updateUi();
    }

    private void discardClicked() {
        if (recorder != null) {
            recorder.discard();
            recorder = null;
            show(getString(R.string.status_dropped));
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
        show(getString(R.string.status_discarded));
        updateUi();
    }

    /** Shows the job state; transient messages from other buttons stay until the next change. */
    private void refreshStatus() {
        if (recorder != null) show(getString(R.string.status_recording));
        else if (finishingRecording) show(getString(R.string.status_finishing));
        else if (job.running) {
            main.removeCallbacks(progressTick);
            main.postDelayed(progressTick, 1000);
        } else if (job.pendingText != null) {
            show(getString(R.string.status_pending, job.pendingText.length(), preview(job.pendingText)));
        } else if (job.audioKept) {
            show(job.error == null ? getString(R.string.status_audio_unfinished)
                    : getString(R.string.status_audio_failed, job.error));
        } else if (job.error != null) show(getString(R.string.status_failed, job.error));
        else if (privateField) show(getString(R.string.status_private_field));
        else show(getString(R.string.status_idle));
        updateUi();
    }

    private String preview(String text) {
        String flat = text.replaceAll("\\s+", " ").trim();
        return getString(R.string.preview_quote, flat.length() > 24 ? flat.substring(0, 24) + "…" : flat);
    }

    private String seconds(long millis) {
        return String.format(Locale.ROOT, getString(R.string.seconds), millis / 1000.0);
    }

    private void renderProgress() {
        if (!job.running || progressBar == null) return;
        progressBar.setVisibility(View.VISIBLE);
        progressBar.setIndeterminate(job.percent < 0);
        if (job.percent >= 0) progressBar.setProgress(job.percent);
        String waited = seconds(SystemClock.elapsedRealtime() - job.started);
        show(job.percent < 0 ? getString(R.string.status_progress, job.stage, waited)
                : getString(R.string.status_progress_percent, job.stage, job.percent, waited));
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
        show(getString(R.string.status_select_all_unsupported));
    }

    private void copyOnce() {
        if (privateField) { show(getString(R.string.status_private_no_copy)); return; }
        InputConnection target = getCurrentInputConnection();
        if (target == null) return;
        CharSequence selected = target.getSelectedText(0);
        if (selected == null || selected.length() == 0) {
            show(getString(R.string.status_select_first));
            return;
        }
        String text = selected.toString();
        lastSystemClip = text;
        addToClipboardHistory(text);
        try {
            clipboard.setPrimaryClip(ClipData.newPlainText(getString(R.string.clip_label), text));
        } catch (RuntimeException ignored) {
            // Still kept in the keyboard's clipboard history.
        }
        show(getString(R.string.status_copied));
    }

    private void pasteLatest() {
        List<String> items = clipboardHistory.items();
        if (!items.isEmpty()) pasteClip(items.get(0));
    }

    private void pasteClip(String text) {
        InputConnection target = getCurrentInputConnection();
        if (target == null) show(getString(R.string.status_tap_field));
        else if (target.commitText(text, 1)) show(getString(R.string.status_pasted));
        else show(getString(R.string.status_paste_failed));
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
        show(getString(R.string.status_clip_deleted));
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
        show(getString(R.string.status_clipboard_cleared));
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
        clipboardTitle.setText(getString(R.string.clipboard_title, items.size(), ClipboardHistory.LIMIT));
        clearClipboardButton.setText(confirmingClear ? R.string.key_confirm_clear : R.string.key_clear);
        clearClipboardButton.setEnabled(!items.isEmpty());
        clipboardList.removeAllViews();
        if (items.isEmpty()) {
            TextView empty = new TextView(this);
            empty.setTextColor(Color.rgb(200, 210, 228));
            empty.setTextSize(14);
            empty.setPadding(dp(4), dp(10), dp(4), dp(10));
            empty.setText(getString(R.string.clipboard_empty, ClipboardHistory.LIMIT));
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
        microphone.setText(recorder == null ? R.string.key_record : R.string.key_stop);
        if (pasteButton != null) pasteButton.setEnabled(!clipboardHistory.items().isEmpty());
        boolean saved = !job.running && (job.pendingText != null || job.audioKept);
        pendingPanel.setVisibility(recorder != null || job.running || saved ? View.VISIBLE : View.GONE);
        pendingAction.setVisibility(saved && recorder == null ? View.VISIBLE : View.GONE);
        pendingAction.setText(job.pendingText != null ? R.string.action_insert : R.string.action_retry);
        pendingAction.setEnabled(job.pendingText == null || !privateField);
        if (recorder != null) discardButton.setText(R.string.action_drop_recording);
        else discardButton.setText(confirmingDiscard ? R.string.action_confirm_discard
                : job.running ? R.string.action_cancel : R.string.action_discard);
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
