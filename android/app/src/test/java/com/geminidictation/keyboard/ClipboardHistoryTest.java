package com.geminidictation.keyboard;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import java.util.Collections;

import org.junit.Test;

public final class ClipboardHistoryTest {
    @Test public void keepsNewestFiftyAndDropsOldest() {
        ClipboardHistory history = new ClipboardHistory();
        for (int i = 1; i <= 52; i++) history.add("item " + i);
        assertEquals(ClipboardHistory.LIMIT, history.items().size());
        assertEquals("item 52", history.items().get(0));
        assertEquals("item 3", history.items().get(49));
    }

    @Test public void copyingAgainMovesToTopWithoutDuplicates() {
        ClipboardHistory history = new ClipboardHistory();
        history.add("a");
        history.add("b");
        assertFalse(history.add("b"));
        assertTrue(history.add("a"));
        assertEquals(Arrays.asList("a", "b"), history.items());
    }

    @Test public void ignoresBlankAndOversizedText() {
        ClipboardHistory history = new ClipboardHistory();
        assertFalse(history.add(null));
        assertFalse(history.add(" \n "));
        assertFalse(history.add(String.join("", Collections.nCopies(ClipboardHistory.MAX_LENGTH + 1, "x"))));
        assertTrue(history.items().isEmpty());
    }

    @Test public void survivesSavingAndLoading() {
        ClipboardHistory history = new ClipboardHistory();
        history.add("第一条\n两行");
        history.add("second");
        ClipboardHistory loaded = ClipboardHistory.deserialize(history.serialize());
        assertEquals(Arrays.asList("second", "第一条\n两行"), loaded.items());
        loaded.remove(0);
        assertEquals(Collections.singletonList("第一条\n两行"), loaded.items());
    }

    @Test public void unreadableDataGivesEmptyHistory() {
        assertTrue(ClipboardHistory.deserialize(new byte[] {0, 0, 0, 1, 0, 0, 0, 5, 1}).items().isEmpty());
        assertTrue(ClipboardHistory.deserialize(new byte[] {9}).items().isEmpty());
        assertTrue(ClipboardHistory.deserialize(null).items().isEmpty());
    }
}
