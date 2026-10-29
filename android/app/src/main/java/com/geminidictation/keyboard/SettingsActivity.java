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

        label(body, getString(R.string.app_name), 23);
        label(body, getString(R.string.settings_intro), 15);
        status = label(body, getString(R.string.settings_ready), 14);

        button(body, getString(R.string.settings_allow_mic), () -> {
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED)
                requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 1);
            else message(getString(R.string.settings_mic_granted));
        });

        label(body, getString(R.string.settings_key_header), 18);
        keyInput = new EditText(this);
        keyInput.setHint(R.string.settings_key_hint);
        keyInput.setSingleLine(true);
        keyInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        body.addView(keyInput);
        keyStatus = label(body, "", 14);
        button(body, getString(R.string.settings_save_key), () -> {
            String value = keyInput.getText().toString().trim();
            if (value.isEmpty()) { message(getString(R.string.settings_paste_key_first)); return; }
            try {
                SecureKeyStore.save(this, value);
                keyInput.setText("");
                keyStatus.setText(R.string.settings_key_encrypted);
                message(getString(R.string.settings_key_saved));
            } catch (Exception error) { message(getString(R.string.settings_key_save_failed, error.getMessage())); }
        });
        button(body, getString(R.string.settings_delete_key), () -> {
            SecureKeyStore.clear(this);
            keyStatus.setText(R.string.settings_no_key);
            message(getString(R.string.settings_key_deleted));
        });
        try {
            keyStatus.setText(SecureKeyStore.read(this).isEmpty()
                    ? R.string.settings_no_key : R.string.settings_key_stored);
        } catch (Exception error) { keyStatus.setText(R.string.settings_key_unreadable); }

        label(body, getString(R.string.settings_vocabulary_header), 18);
        label(body, getString(R.string.settings_vocabulary_intro), 15);
        fileStatus = label(body, getString(VocabularyStore.selectedUri(this) == null
                ? R.string.settings_no_file : R.string.settings_file_chosen), 14);
        button(body, getString(R.string.settings_choose_file), () -> requestPickFile(false));
        button(body, getString(R.string.settings_create_file), () -> requestPickFile(true));
        label(body, getString(R.string.settings_add_header), 16);
        termInput = new EditText(this);
        termInput.setHint(R.string.settings_term_hint);
        termInput.setSingleLine(true);
        termInput.setImeOptions(EditorInfo.IME_ACTION_DONE);
        termInput.setOnEditorActionListener((view, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_DONE) { addTerm(); return true; }
            return false;
        });
        body.addView(termInput);
        addVocabularyButton = button(body, getString(R.string.settings_add_save), this::addTerm);
        label(body, getString(R.string.settings_add_note), 14);
        filterInput = new EditText(this);
        filterInput.setHint(R.string.settings_filter_hint);
        filterInput.setSingleLine(true);
        filterInput.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence text, int start, int count, int after) { }
            @Override public void onTextChanged(CharSequence text, int start, int before, int count) { renderTerms(); }
            @Override public void afterTextChanged(Editable text) { }
        });
        body.addView(filterInput);
        label(body, getString(R.string.settings_list_note), 14);
        vocabularyCount = label(body, getResources().getQuantityString(R.plurals.vocabulary_total, 0, 0), 14);
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
        button(body, getString(R.string.settings_reload), this::requestReload);
        saveVocabularyButton = button(body, getString(R.string.settings_save_changes), this::saveVocabulary);
        saveVocabularyButton.setEnabled(false);

        label(body, getString(R.string.settings_ime_header), 18);
        button(body, getString(R.string.settings_enable_ime),
                () -> startActivity(new Intent(Settings.ACTION_INPUT_METHOD_SETTINGS)));
        button(body, getString(R.string.settings_pick_ime), () -> {
            InputMethodManager manager = (InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
            manager.showInputMethodPicker();
        });
        label(body, getString(R.string.settings_usage), 15);
        if (savedInstanceState != null && savedInstanceState.containsKey("draft")) {
            draft = new VocabularyDraft(savedInstanceState.getString("draft", ""));
            loadedVocabulary = savedInstanceState.getString("loaded");
            dirty = savedInstanceState.getBoolean("dirty");
            renderTerms();
            saveVocabularyButton.setEnabled(dirty);
        } else if (VocabularyStore.selectedUri(this) != null) loadVocabulary();
    }

    private void requestPickFile(boolean create) {
        if (saving || loading) { message(getString(R.string.settings_wait_busy)); return; }
        if (!dirty) { pickFile(create); return; }
        new AlertDialog.Builder(this)
                .setMessage(R.string.settings_switch_discards)
                .setNegativeButton(R.string.cancel, null)
                .setPositiveButton(R.string.settings_continue, (dialog, which) -> pickFile(create))
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
            fileStatus.setText(getString(R.string.settings_file_granted, data.getData().getLastPathSegment()));
            loadVocabulary();
        } catch (Exception error) { message(getString(R.string.settings_file_grant_failed, describe(error))); }
    }

    private void requestReload() {
        if (saving) { message(getString(R.string.settings_wait_saving)); return; }
        if (!dirty) { loadVocabulary(); return; }
        new AlertDialog.Builder(this)
                .setMessage(R.string.settings_reload_discards)
                .setNegativeButton(R.string.cancel, null)
                .setPositiveButton(R.string.settings_reload_confirm, (dialog, which) -> loadVocabulary())
                .show();
    }

    private void loadVocabulary() {
        if (VocabularyStore.selectedUri(this) == null) {
            message(getString(R.string.settings_choose_file_first));
            return;
        }
        if (loading || saving) return;
        loading = true;
        addVocabularyButton.setEnabled(false);
        message(getString(R.string.settings_loading));
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
                    int count = draft.terms().size();
                    message(getResources().getQuantityString(R.plurals.vocabulary_loaded, count, count));
                });
            } catch (Exception error) { runOnUiThread(() -> {
                loading = false;
                addVocabularyButton.setEnabled(true);
                message(getString(R.string.settings_load_failed, describe(error)));
            }); }
        });
    }

    private void addTerm() {
        if (saving || loading) { message(getString(R.string.settings_wait_busy)); return; }
        if (loadedVocabulary == null) { message(getString(R.string.settings_load_first)); return; }
        try {
            draft.add(termInput.getText().toString());
            termInput.setText("");
            markDirty();
            saveVocabulary();
        } catch (IllegalArgumentException error) {
            termInput.setError(describe(error));
        }
    }

    private void editTerm(String old) {
        if (old == null) return;
        if (saving || loading) { message(getString(R.string.settings_wait_busy)); return; }
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setText(old);
        input.selectAll();
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle(R.string.settings_edit_term)
                .setView(input)
                .setNegativeButton(R.string.cancel, null)
                .setPositiveButton(R.string.settings_save, null)
                .create();
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view -> {
            try {
                draft.replace(old, input.getText().toString());
                markDirty();
                dialog.dismiss();
            } catch (IllegalArgumentException error) { input.setError(describe(error)); }
        }));
        dialog.show();
    }

    private void confirmDelete(String term) {
        if (term == null) return;
        if (saving || loading) { message(getString(R.string.settings_wait_busy)); return; }
        new AlertDialog.Builder(this)
                .setMessage(getString(R.string.settings_delete_term, term))
                .setNegativeButton(R.string.cancel, null)
                .setPositiveButton(R.string.settings_delete, (dialog, which) -> {
                    draft.remove(term);
                    markDirty();
                }).show();
    }

    private void markDirty() {
        dirty = true;
        renderTerms();
        saveVocabularyButton.setEnabled(true);
        message(getString(R.string.settings_list_updated));
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
        int total = draft.terms().size();
        vocabularyCount.setText(getResources().getQuantityString(R.plurals.vocabulary_shown, total,
                vocabularyAdapter.getCount(), total));
    }

    private void saveVocabulary() {
        if (saving || loading) return;
        String text = draft.serialize();
        String expected = loadedVocabulary;
        Uri uri = VocabularyStore.selectedUri(this);
        if (expected == null) { message(getString(R.string.settings_load_before_save)); return; }
        if (!dirty) { message(getString(R.string.settings_nothing_to_save)); return; }
        message(getString(R.string.settings_saving));
        saving = true;
        addVocabularyButton.setEnabled(false);
        saveVocabularyButton.setEnabled(false);
        worker.execute(() -> {
            try {
                if (!VocabularyStore.read(this, uri).equals(expected))
                    throw new IOException(getString(R.string.settings_conflict));
                VocabularyStore.write(this, uri, text);
                runOnUiThread(() -> {
                    loadedVocabulary = text;
                    saving = false;
                    addVocabularyButton.setEnabled(true);
                    dirty = false;
                    int count = draft.terms().size();
                    message(getResources().getQuantityString(R.plurals.vocabulary_saved, count, count));
                });
            } catch (Exception error) { runOnUiThread(() -> {
                saving = false;
                addVocabularyButton.setEnabled(true);
                saveVocabularyButton.setEnabled(true);
                message(getString(R.string.settings_save_failed, describe(error)));
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

    private String describe(Exception error) {
        return error instanceof VocabularyDraft.InvalidTermException invalid
                ? getString(invalid.messageId) : error.getMessage();
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
