package com.geminidictation.keyboard;

import java.text.Collator;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;

/** Editable view of one vocabulary file. Comments survive edits; terms are saved in sorted order. */
final class VocabularyDraft {
    /** A rejected edit. Carries a string resource id so this class stays free of Android APIs. */
    static final class InvalidTermException extends IllegalArgumentException {
        final int messageId;

        InvalidTermException(int messageId) { this.messageId = messageId; }
    }

    private final List<String> comments = new ArrayList<>();
    private final List<String> terms = new ArrayList<>();

    VocabularyDraft(String text) {
        for (String line : text.split("\\R")) {
            String value = line.trim();
            if (value.startsWith("#")) comments.add(line);
            else if (!value.isEmpty() && !containsIgnoreCase(value, null)) terms.add(value);
        }
        if (terms.size() > 1000) throw new InvalidTermException(R.string.error_term_limit);
        sort();
    }

    List<String> terms() { return Collections.unmodifiableList(terms); }

    void add(String input) {
        String value = clean(input);
        if (containsIgnoreCase(value, null)) throw new InvalidTermException(R.string.error_term_exists);
        if (terms.size() >= 1000) throw new InvalidTermException(R.string.error_term_limit);
        terms.add(value);
        sort();
    }

    void replace(String old, String input) {
        if (!terms.contains(old)) throw new InvalidTermException(R.string.error_term_not_found);
        String value = clean(input);
        if (containsIgnoreCase(value, old)) throw new InvalidTermException(R.string.error_term_exists);
        terms.set(terms.indexOf(old), value);
        sort();
    }

    void remove(String value) { terms.remove(value); }

    String serialize() {
        StringBuilder result = new StringBuilder();
        for (String comment : comments) result.append(comment).append('\n');
        for (String term : terms) result.append(term).append('\n');
        return result.toString();
    }

    private boolean containsIgnoreCase(String value, String except) {
        for (String term : terms) {
            if (!term.equals(except) && term.equalsIgnoreCase(value)) return true;
        }
        return false;
    }

    private static String clean(String input) {
        String value = input.trim();
        if (value.isEmpty() || value.startsWith("#") || value.contains("\n") || value.contains("\r"))
            throw new InvalidTermException(R.string.error_term_invalid);
        return value;
    }

    private void sort() {
        Collator chinese = Collator.getInstance(Locale.CHINA);
        chinese.setStrength(Collator.PRIMARY);
        terms.sort((left, right) -> {
            boolean leftAscii = left.codePointAt(0) < 128;
            boolean rightAscii = right.codePointAt(0) < 128;
            if (leftAscii != rightAscii) return leftAscii ? -1 : 1;
            int order = chinese.compare(left, right);
            return order != 0 ? order : left.compareToIgnoreCase(right);
        });
    }
}
