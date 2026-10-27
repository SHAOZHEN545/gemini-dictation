package com.geminidictation.keyboard;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Copied texts, newest first. Holds at most {@link #LIMIT}; adding more drops the oldest. */
final class ClipboardHistory {
    static final int LIMIT = 50;
    /** Longer copies (for example a whole document) are not kept. */
    static final int MAX_LENGTH = 100_000;
    private static final int FORMAT = 1;

    private final List<String> items = new ArrayList<>();

    List<String> items() { return Collections.unmodifiableList(items); }

    /** Returns false when nothing changed. Copying an existing text again moves it to the top. */
    boolean add(CharSequence text) {
        if (text == null || text.length() > MAX_LENGTH || text.toString().trim().isEmpty()) return false;
        String value = text.toString();
        if (!items.isEmpty() && items.get(0).equals(value)) return false;
        items.remove(value);
        items.add(0, value);
        while (items.size() > LIMIT) items.remove(items.size() - 1);
        return true;
    }

    void remove(int index) { items.remove(index); }

    void clear() { items.clear(); }

    byte[] serialize() {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        try (DataOutputStream output = new DataOutputStream(bytes)) {
            output.writeInt(FORMAT);
            output.writeInt(items.size());
            for (String item : items) {
                byte[] encoded = item.getBytes(StandardCharsets.UTF_8);
                output.writeInt(encoded.length);
                output.write(encoded);
            }
        } catch (IOException impossible) {
            throw new IllegalStateException(impossible);
        }
        return bytes.toByteArray();
    }

    /** Reads saved history; unreadable data gives an empty history rather than an error. */
    static ClipboardHistory deserialize(byte[] data) {
        ClipboardHistory history = new ClipboardHistory();
        if (data == null) return history;
        try (DataInputStream input = new DataInputStream(new ByteArrayInputStream(data))) {
            if (input.readInt() != FORMAT) return history;
            int count = input.readInt();
            List<String> loaded = new ArrayList<>();
            for (int i = 0; i < count; i++) {
                int length = input.readInt();
                if (length < 0 || length > input.available()) return history;
                byte[] encoded = new byte[length];
                input.readFully(encoded);
                loaded.add(new String(encoded, StandardCharsets.UTF_8));
            }
            for (int i = loaded.size() - 1; i >= 0; i--) history.add(loaded.get(i));
        } catch (IOException corrupt) {
            history.clear();
        }
        return history;
    }
}
