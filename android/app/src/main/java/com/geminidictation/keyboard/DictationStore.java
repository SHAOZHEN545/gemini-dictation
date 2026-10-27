package com.geminidictation.keyboard;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;

/**
 * Keeps the latest recording, and its transcript until it is typed, in app-private storage.
 * This lets a dictation survive the keyboard closing, the IME restarting, or a failed upload.
 */
final class DictationStore {
    private final File audio;
    private final File text;

    DictationStore(File directory) {
        audio = new File(directory, "pending-recording.pcm");
        text = new File(directory, "pending-transcript.txt");
    }

    void saveAudio(byte[] pcm) throws IOException { write(audio, pcm); }

    byte[] readAudio() throws IOException {
        if (!audio.isFile()) throw new IOException("录音文件已不存在，请重新录音");
        return Files.readAllBytes(audio.toPath());
    }

    boolean hasAudio() { return audio.isFile(); }

    void saveText(String transcript) throws IOException {
        write(text, transcript.getBytes(StandardCharsets.UTF_8));
    }

    String readText() {
        if (!text.isFile()) return null;
        try {
            return new String(Files.readAllBytes(text.toPath()), StandardCharsets.UTF_8);
        } catch (IOException error) {
            return null;
        }
    }

    void clear() {
        audio.delete();
        text.delete();
    }

    static void write(File target, byte[] data) throws IOException {
        File temporary = new File(target.getPath() + ".tmp");
        try (FileOutputStream output = new FileOutputStream(temporary)) {
            output.write(data);
            output.getFD().sync();
        }
        if (!temporary.renameTo(target)) {
            temporary.delete();
            throw new IOException("无法保存 " + target.getName());
        }
    }
}
