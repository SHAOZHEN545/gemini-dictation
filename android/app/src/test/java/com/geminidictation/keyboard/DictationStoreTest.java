package com.geminidictation.keyboard;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;

import org.junit.Test;

public final class DictationStoreTest {
    @Test public void keepsRecordingAndTranscriptUntilCleared() throws IOException {
        File directory = Files.createTempDirectory("dictation").toFile();
        DictationStore store = new DictationStore(directory);
        assertFalse(store.hasAudio());
        assertNull(store.readText());

        byte[] pcm = {1, 2, 3, 4};
        store.saveAudio(pcm);
        store.saveText("你好 world");
        // A new instance stands in for the keyboard process restarting.
        DictationStore reopened = new DictationStore(directory);
        assertTrue(reopened.hasAudio());
        assertArrayEquals(pcm, reopened.readAudio());
        assertEquals("你好 world", reopened.readText());

        reopened.clear();
        assertFalse(store.hasAudio());
        assertNull(store.readText());
        assertThrows(IOException.class, store::readAudio);
        assertEquals(0, directory.list().length);
    }
}
