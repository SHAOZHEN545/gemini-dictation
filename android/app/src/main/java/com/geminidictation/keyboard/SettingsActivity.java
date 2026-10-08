package com.geminidictation.keyboard;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.provider.Settings;
import android.text.Editable;
import android.text.InputType;
import android.text.TextWatcher;
import android.view.MotionEvent;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputMethodManager;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ListView;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.IOException;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class SettingsActivity extends Activity {
    private static final int OPEN_FILE = 10;
    private static final int CREATE_FILE = 11;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private TextView status;
    private TextView fileStatus;
    private TextView keyStatus;
    private TextView vocabularyCount;
    private EditText keyInput;
    private EditText termInput;
    private EditText filterInput;
    private ArrayAdapter<String> vocabularyAdapter;
    private VocabularyDraft draft = new VocabularyDraft("");
    private Button saveVocabularyButton;
    private Button addVocabularyButton;
    private boolean dirty;
    private boolean saving;
    private boolean loading;
    private volatile String loadedVocabulary;

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        ScrollView scroll = new ScrollView(this);
        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        int pad = dp(18);
        body.setPadding(pad, pad, pad, pad);
        scroll.addView(body);
        setContentView(scroll);

        label(body, "Gemini 语音输入", 23);
        label(body, "先授权麦克风、保存自己的 Gemini API Key，再选 Google Drive 中的词库文本文件。", 15);
        status = label(body, "准备就绪", 14);

        button(body, "授权麦克风", () -> {
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED)
                requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 1);
            else message("麦克风已授权");
        });

        label(body, "Gemini API Key（只保存在本机）", 18);
        keyInput = new EditText(this);
        keyInput.setHint("从 Bitwarden 粘贴 Key");
        keyInput.setSingleLine(true);
        keyInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        body.addView(keyInput);
        keyStatus = label(body, "", 14);
        button(body, "保存 Key", () -> {
            String value = keyInput.getText().toString().trim();
            if (value.isEmpty()) { message("请先粘贴 Key"); return; }
            try {
                SecureKeyStore.save(this, value);
                keyInput.setText("");
                keyStatus.setText("Key 已加密保存在本机");
                message("Key 已保存");
            } catch (Exception error) { message("Key 保存失败：" + error.getMessage()); }
        });
        button(body, "删除本机 Key", () -> {
            SecureKeyStore.clear(this);
            keyStatus.setText("未保存 Key");
            message("本机 Key 已删除");
        });
        try { keyStatus.setText(SecureKeyStore.read(this).isEmpty() ? "未保存 Key" : "Key 已保存在本机"); }
        catch (Exception error) { keyStatus.setText("Key 无法读取，请重新保存"); }

        label(body, "个人词库 · Google Drive", 18);
        label(body, "点“选择”后在系统文件选择器中进入 Google Drive，选一份可编辑的 .txt 文件。应用只获得这一个文件的读写权限。", 15);
        fileStatus = label(body, VocabularyStore.selectedUri(this) == null ? "尚未选择文件" : "已选择词库文件", 14);
        button(body, "选择已有词库文件", () -> requestPickFile(false));
        button(body, "在 Google Drive 新建词库文件", () -> requestPickFile(true));
        label(body, "添加词条（每次输入一个）", 16);
        termInput = new EditText(this);
        termInput.setHint("输入词条后点添加并保存或键盘完成键");
        termInput.setSingleLine(true);
        termInput.setImeOptions(EditorInfo.IME_ACTION_DONE);
        termInput.setOnEditorActionListener((view, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_DONE) { addTerm(); return true; }
            return false;
        });
        body.addView(termInput);
        addVocabularyButton = button(body, "添加并保存", this::addTerm);
        label(body, "添加后自动保存到 Drive；修改和删除词条后仍需点“保存修改到 Drive”。", 14);
        filterInput = new EditText(this);
        filterInput.setHint("筛选词条");
        filterInput.setSingleLine(true);
        filterInput.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence text, int start, int count, int after) { }
            @Override public void onTextChanged(CharSequence text, int start, int before, int count) { renderTerms(); }
            @Override public void afterTextChanged(Editable text) { }
        });
        body.addView(filterInput);
        label(body, "词条按字母和拼音排列；在下方列表内滑动浏览，点词条修改，长按删除。", 14);
        vocabularyCount = label(body, "共 0 个词条", 14);
        ListView list = new ListView(this);
        vocabularyAdapter = new ArrayAdapter<>(this, android.R.layout.simple_list_item_1);
        list.setAdapter(vocabularyAdapter);
        list.setVerticalScrollBarEnabled(true);
        list.setScrollbarFadingEnabled(false);
        list.setFastScrollEnabled(true);
        list.setFastScrollAlwaysVisible(true);
        list.setOnTouchListener((view, event) -> {
            int action = event.getActionMasked();
            if (action == MotionEvent.ACTION_DOWN)
                view.getParent().requestDisallowInterceptTouchEvent(true);
            else if (action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL)
                view.getParent().requestDisallowInterceptTouchEvent(false);
            return false;
        });
        list.setOnItemClickListener((parent, view, position, id) -> editTerm(vocabularyAdapter.getItem(position)));
        list.setOnItemLongClickListener((parent, view, position, id) -> {
            confirmDelete(vocabularyAdapter.getItem(position));
            return true;
        });
        list.setLayoutParams(new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(360)));
        body.addView(list);
        button(body, "从 Drive 重新读取", this::requestReload);
        saveVocabularyButton = button(body, "保存修改到 Drive", this::saveVocabulary);
        saveVocabularyButton.setEnabled(false);

        label(body, "输入法设置", 18);
        button(body, "启用 Gemini 语音输入", () -> startActivity(new Intent(Settings.ACTION_INPUT_METHOD_SETTINGS)));
        button(body, "选择当前输入法", () -> {
            InputMethodManager manager = (InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
            manager.showInputMethodPicker();
        });
        label(body, "在其他 App 的输入框里切换到 Gemini 语音输入。点击麦克风开始录音，再点一次结束；转写完成后文字会写入当前输入框。", 15);
        if (savedInstanceState != null && savedInstanceState.containsKey("draft")) {
            draft = new VocabularyDraft(savedInstanceState.getString("draft", ""));
            loadedVocabulary = savedInstanceState.getString("loaded");
            dirty = savedInstanceState.getBoolean("dirty");
            renderTerms();
            saveVocabularyButton.setEnabled(dirty);
        } else if (VocabularyStore.selectedUri(this) != null) loadVocabulary();
    }

    private void requestPickFile(boolean create) {
        if (saving || loading) { message("请等词库读取或保存完成"); return; }
        if (!dirty) { pickFile(create); return; }
        new AlertDialog.Builder(this)
                .setMessage("切换词库文件会放弃尚未保存的词条修改。")
                .setNegativeButton("取消", null)
                .setPositiveButton("继续", (dialog, which) -> pickFile(create))
                .show();
    }

    private void pickFile(boolean create) {
        Intent intent = new Intent(create ? Intent.ACTION_CREATE_DOCUMENT : Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("text/plain");
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION |
                Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
        if (create) intent.putExtra(Intent.EXTRA_TITLE, "gemini-personal-vocabulary.txt");
        startActivityForResult(intent, create ? CREATE_FILE : OPEN_FILE);
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if ((requestCode != OPEN_FILE && requestCode != CREATE_FILE) || resultCode != RESULT_OK || data == null) return;
        try {
            VocabularyStore.select(this, data);
            loadedVocabulary = null;
            draft = new VocabularyDraft("");
            dirty = false;
            renderTerms();
            saveVocabularyButton.setEnabled(false);
            fileStatus.setText("已授权读取和写入：" + data.getData().getLastPathSegment());
            loadVocabulary();
        } catch (Exception error) { message("词库授权失败：" + error.getMessage()); }
    }

    private void requestReload() {
        if (saving) { message("请等词库保存完成"); return; }
        if (!dirty) { loadVocabulary(); return; }
        new AlertDialog.Builder(this)
                .setMessage("重新读取会放弃尚未保存的词条修改。")
                .setNegativeButton("取消", null)
                .setPositiveButton("重新读取", (dialog, which) -> loadVocabulary())
                .show();
    }

    private void loadVocabulary() {
        if (VocabularyStore.selectedUri(this) == null) { message("请先选择 Drive 词库文件"); return; }
        if (loading || saving) return;
        loading = true;
        addVocabularyButton.setEnabled(false);
        message("正在从 Drive 读取词库…");
        worker.execute(() -> {
            try {
                String text = VocabularyStore.read(this);
                VocabularyDraft next = new VocabularyDraft(text);
                runOnUiThread(() -> {
                    loading = false;
                    addVocabularyButton.setEnabled(true);
                    draft = next;
                    loadedVocabulary = text;
                    dirty = false;
                    saveVocabularyButton.setEnabled(false);
                    renderTerms();
                    message("已读取 " + draft.terms().size() + " 个词条");
                });
            } catch (Exception error) { runOnUiThread(() -> {
                loading = false;
                addVocabularyButton.setEnabled(true);
                message("读取失败：" + error.getMessage());
            }); }
        });
    }

    private void addTerm() {
        if (saving || loading) { message("请等词库读取或保存完成"); return; }
        if (loadedVocabulary == null) { message("请先选择并读取 Drive 词库文件"); return; }
        try {
            draft.add(termInput.getText().toString());
            termInput.setText("");
            markDirty();
            saveVocabulary();
        } catch (IllegalArgumentException error) {
            termInput.setError(error.getMessage());
        }
    }

    private void editTerm(String old) {
        if (old == null) return;
        if (saving || loading) { message("请等词库读取或保存完成"); return; }
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setText(old);
        input.selectAll();
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("修改词条")
                .setView(input)
                .setNegativeButton("取消", null)
                .setPositiveButton("保存", null)
                .create();
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view -> {
            try {
                draft.replace(old, input.getText().toString());
                markDirty();
                dialog.dismiss();
            } catch (IllegalArgumentException error) { input.setError(error.getMessage()); }
        }));
        dialog.show();
    }

    private void confirmDelete(String term) {
        if (term == null) return;
        if (saving || loading) { message("请等词库读取或保存完成"); return; }
        new AlertDialog.Builder(this)
                .setMessage("删除词条“" + term + "”？")
                .setNegativeButton("取消", null)
                .setPositiveButton("删除", (dialog, which) -> {
                    draft.remove(term);
                    markDirty();
                }).show();
    }

    private void markDirty() {
        dirty = true;
        renderTerms();
        saveVocabularyButton.setEnabled(true);
        message("已更新列表 · 点击“保存修改到 Drive”同步");
    }

    private void renderTerms() {
        if (vocabularyAdapter == null) return;
        String filter = filterInput.getText().toString().trim().toLowerCase(Locale.ROOT);
        vocabularyAdapter.setNotifyOnChange(false);
        vocabularyAdapter.clear();
        for (String term : draft.terms()) {
            if (term.toLowerCase(Locale.ROOT).contains(filter)) vocabularyAdapter.add(term);
        }
        vocabularyAdapter.notifyDataSetChanged();
        vocabularyCount.setText("显示 " + vocabularyAdapter.getCount() + " / 共 " + draft.terms().size() + " 个词条");
    }

    private void saveVocabulary() {
        if (saving || loading) return;
        String text = draft.serialize();
        String expected = loadedVocabulary;
        Uri uri = VocabularyStore.selectedUri(this);
        if (expected == null) { message("请先读取词库，再保存修改"); return; }
        if (!dirty) { message("词库没有待保存的修改"); return; }
        message("正在保存到 Drive…");
        saving = true;
        addVocabularyButton.setEnabled(false);
        saveVocabularyButton.setEnabled(false);
        worker.execute(() -> {
            try {
                if (!VocabularyStore.read(this, uri).equals(expected))
                    throw new IOException("云端词库已被其他设备修改，请先重新读取，以免覆盖新词条");
                VocabularyStore.write(this, uri, text);
                runOnUiThread(() -> {
                    loadedVocabulary = text;
                    saving = false;
                    addVocabularyButton.setEnabled(true);
                    dirty = false;
                    message("词库已保存到 Drive · " + draft.terms().size() + " 个词条");
                });
            } catch (Exception error) { runOnUiThread(() -> {
                saving = false;
                addVocabularyButton.setEnabled(true);
                saveVocabularyButton.setEnabled(true);
                message("保存失败，修改仍在当前列表中，请点击“保存修改到 Drive”重试：" + error.getMessage());
            }); }
        });
    }

    private TextView label(LinearLayout parent, String text, int size) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextSize(size);
        view.setPadding(0, dp(9), 0, dp(6));
        parent.addView(view);
        return view;
    }

    private Button button(LinearLayout parent, String text, Runnable action) {
        Button button = new Button(this);
        button.setText(text);
        button.setAllCaps(false);
        button.setOnClickListener(view -> action.run());
        parent.addView(button);
        return button;
    }

    private void message(String text) { status.setText(text); }
    private int dp(int value) { return (int) (value * getResources().getDisplayMetrics().density + .5f); }

    @Override protected void onSaveInstanceState(Bundle outState) {
        outState.putString("draft", draft.serialize());
        outState.putString("loaded", loadedVocabulary);
        outState.putBoolean("dirty", dirty);
        super.onSaveInstanceState(outState);
    }

    @Override protected void onDestroy() {
        // Let an accepted save finish even if the user leaves this screen.
        worker.shutdown();
        super.onDestroy();
    }
}
