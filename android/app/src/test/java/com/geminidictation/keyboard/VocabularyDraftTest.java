package com.geminidictation.keyboard;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

import java.util.Arrays;

import org.junit.Test;

public final class VocabularyDraftTest {
    @Test public void addingAndEditingSortImmediatelyWithoutLosingComments() {
        VocabularyDraft draft = new VocabularyDraft("# Private terms\nzeta\nAlpha\n中文\nbeta\nalpha\n");
        assertEquals(Arrays.asList("Alpha", "beta", "zeta", "中文"), draft.terms());
        draft.add("Gamma");
        assertEquals(Arrays.asList("Alpha", "beta", "Gamma", "zeta", "中文"), draft.terms());
        draft.replace("zeta", "Aardvark");
        assertEquals("# Private terms\nAardvark\nAlpha\nbeta\nGamma\n中文\n", draft.serialize());
        assertThrows(IllegalArgumentException.class, () -> draft.add("alpha"));
    }

    @Test public void rejectsInvalidTermsAndPreservesEmptyFile() {
        VocabularyDraft draft = new VocabularyDraft("");
        assertEquals("", draft.serialize());
        assertThrows(IllegalArgumentException.class, () -> draft.add("# comment"));
        assertThrows(IllegalArgumentException.class, () -> draft.add("one\ntwo"));
    }
}
