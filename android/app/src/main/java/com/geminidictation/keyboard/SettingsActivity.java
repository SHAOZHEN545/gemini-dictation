package com.geminidictation.keyboard;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.view.View;
import android.view.inputmethod.InputMethodManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.List;
import java.io.IOException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class SettingsActivity extends Activity {
    private static final int OPEN_FILE = 10;
    private static final int CREATE_FILE = 11;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private TextView status;
    private TextView fileStatus;
    private TextView keyStatus;
    private EditText keyInput;
    private EditText vocabularyInput;
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
        button(body, "选择已有词库文件", () -> pickFile(false));
        button(body, "在 Google Drive 新建词库文件", () -> pickFile(true));
        vocabularyInput = new EditText(this);
        vocabularyInput.setHint("每行一个词条；# 开头的行是注释");
        vocabularyInput.setMinLines(10);
        vocabularyInput.setGravity(android.view.Gravity.TOP);
        vocabularyInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE |
                InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        body.addView(vocabularyInput);
        button(body, "从 Drive 重新读取", this::loadVocabulary);
        button(body, "保存词库到 Drive", this::saveVocabulary);

        label(body, "输入法设置", 18);
        button(body, "启用 Gemini 语音输入", () -> startActivity(new Intent(Settings.ACTION_INPUT_METHOD_SETTINGS)));
        button(body, "选择当前输入法", () -> {
            InputMethodManager manager = (InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
            manager.showInputMethodPicker();
        });
        label(body, "在其他 App 的输入框里切换到 Gemini 语音输入。点击麦克风开始录音，再点一次结束；转写完成后文字会写入当前输入框。", 15);
        if (VocabularyStore.selectedUri(this) != null) loadVocabulary();
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
            fileStatus.setText("已授权读取和写入：" + data.getData().getLastPathSegment());
            loadVocabulary();
        } catch (Exception error) { message("词库授权失败：" + error.getMessage()); }
    }

    private void loadVocabulary() {
        message("正在从 Drive 读取词库…");
        worker.execute(() -> {
            try {
                String text = VocabularyStore.read(this);
                List<String> terms = VocabularyStore.parse(text);
                runOnUiThread(() -> {
                    vocabularyInput.setText(text);
                    loadedVocabulary = text;
                    message("已读取 " + terms.size() + " 个词条");
                });
            } catch (Exception error) { runOnUiThread(() -> message("读取失败：" + error.getMessage())); }
        });
    }

    private void saveVocabulary() {
        String text = vocabularyInput.getText().toString();
        String expected = loadedVocabulary;
        if (expected == null) { message("请先读取词库，再保存修改"); return; }
        try { VocabularyStore.parse(text); }
        catch (Exception error) { message(error.getMessage()); return; }
        message("正在保存到 Drive…");
        worker.execute(() -> {
            try {
                if (!VocabularyStore.read(this).equals(expected))
                    throw new IOException("云端词库已被其他设备修改，请先重新读取，以免覆盖新词条");
                VocabularyStore.write(this, text);
                runOnUiThread(() -> {
                    loadedVocabulary = text;
                    message("词库已保存到选中的文件");
                });
            } catch (Exception error) { runOnUiThread(() -> message("保存失败：" + error.getMessage())); }
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

    private void button(LinearLayout parent, String text, Runnable action) {
        Button button = new Button(this);
        button.setText(text);
        button.setAllCaps(false);
        button.setOnClickListener(view -> action.run());
        parent.addView(button);
    }

    private void message(String text) { status.setText(text); }
    private int dp(int value) { return (int) (value * getResources().getDisplayMetrics().density + .5f); }

    @Override protected void onDestroy() {
        worker.shutdownNow();
        super.onDestroy();
    }
}
