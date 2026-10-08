package com.geminidictation.keyboard;

import android.content.Context;
import android.content.Intent;
import android.net.Uri;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;

final class VocabularyStore {
    private static final String PREFS = "settings";
    private static final String URI_KEY = "vocabulary_uri";

    private VocabularyStore() { }

    static Uri selectedUri(Context context) {
        String value = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(URI_KEY, null);
        return value == null ? null : Uri.parse(value);
    }

    static void select(Context context, Intent result) {
        Uri uri = result.getData();
        if (uri == null) throw new IllegalArgumentException("没有选中文件");
        int flags = result.getFlags() & (Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
        if ((flags & Intent.FLAG_GRANT_READ_URI_PERMISSION) == 0 ||
                (flags & Intent.FLAG_GRANT_WRITE_URI_PERMISSION) == 0) {
            throw new SecurityException("这个文件没有同时授予读写权限，请选择可编辑的文本文件");
        }
        context.getContentResolver().takePersistableUriPermission(uri, flags);
        Uri old = selectedUri(context);
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(URI_KEY, uri.toString()).apply();
        if (old != null && !old.equals(uri)) {
            try {
                context.getContentResolver().releasePersistableUriPermission(old,
                        Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
            } catch (SecurityException ignored) {
                // The earlier provider may already have revoked its grant.
            }
        }
    }

    static String read(Context context) throws IOException {
        return read(context, selectedUri(context));
    }

    static String read(Context context, Uri uri) throws IOException {
        if (uri == null) return "";
        try (InputStream stream = context.getContentResolver().openInputStream(uri)) {
            if (stream == null) throw new IOException("无法读取词库文件");
            BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8));
            StringBuilder text = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) {
                if (text.length() > 256_000) throw new IOException("词库文件过大");
                text.append(line).append('\n');
            }
            return text.toString();
        } catch (SecurityException error) {
            throw new IOException("词库授权已失效，请重新选择文件", error);
        }
    }

    static void write(Context context, String text) throws IOException {
        write(context, selectedUri(context), text);
    }

    static void write(Context context, Uri uri, String text) throws IOException {
        if (uri == null) throw new IOException("请先选择 Google Drive 中的词库文件");
        if (text.getBytes(StandardCharsets.UTF_8).length > 256_000) throw new IOException("词库文件过大");
        parse(text); // Do not replace a valid cloud file with an invalid list.
        try (OutputStream stream = context.getContentResolver().openOutputStream(uri, "wt")) {
            if (stream == null) throw new IOException("这个文件无法写入");
            stream.write(text.getBytes(StandardCharsets.UTF_8));
            stream.flush();
        } catch (SecurityException error) {
            throw new IOException("词库写入权限已失效，请重新选择文件", error);
        }
    }

    static List<String> parse(String text) {
        LinkedHashSet<String> unique = new LinkedHashSet<>();
        for (String line : text.split("\\R")) {
            String term = line.trim();
            if (!term.isEmpty() && !term.startsWith("#")) unique.add(term);
        }
        if (unique.size() > 1000) throw new IllegalArgumentException("Gemini 最多接受 1000 个词条");
        return new ArrayList<>(unique);
    }
}
