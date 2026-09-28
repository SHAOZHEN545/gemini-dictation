package com.geminidictation.keyboard;

import java.text.Collator;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;

/** Editable view of one vocabulary file. Comments survive edits; terms are saved in sorted order. */
final class VocabularyDraft {
    private final List<String> comments = new ArrayList<>();
    private final List<String> terms = new ArrayList<>();

    VocabularyDraft(String text) {
        for (String line : text.split("\\R")) {
            String value = line.trim();
            if (value.startsWith("#")) comments.add(line);
            else if (!value.isEmpty() && !containsIgnoreCase(value, null)) terms.add(value);
        }
        if (terms.size() > 1000) throw new IllegalArgumentException("Gemini 最多接受 1000 个词条");
        sort();
    }

    List<String> terms() { return Collections.unmodifiableList(terms); }

    void add(String input) {
        String value = clean(input);
        if (containsIgnoreCase(value, null)) throw new IllegalArgumentException("词库里已经有这个词条");
        if (terms.size() >= 1000) throw new IllegalArgumentException("Gemini 最多接受 1000 个词条");
        terms.add(value);
        sort();
    }

    void replace(String old, String input) {
        if (!terms.contains(old)) throw new IllegalArgumentException("找不到要修改的词条");
        String value = clean(input);
        if (containsIgnoreCase(value, old)) throw new IllegalArgumentException("词库里已经有这个词条");
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
            throw new IllegalArgumentException("请输入一个非空词条，不要包含换行或以 # 开头");
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
