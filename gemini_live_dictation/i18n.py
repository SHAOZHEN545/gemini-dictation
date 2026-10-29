"""Interface language: English by default, Simplified Chinese when the system UI language is Chinese.

Strings are written in English in the code and looked up in ZH when the interface is Chinese.
Set GEMINI_DICTATION_LANG=en or zh to override the system language.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QLocale


LANGUAGE_VARIABLE = "GEMINI_DICTATION_LANG"


def detect_language() -> str:
    override = os.environ.get(LANGUAGE_VARIABLE, "").strip().lower()[:2]
    if override in ("en", "zh"):
        return override
    languages = QLocale.system().uiLanguages()
    return "zh" if languages and languages[0].lower().startswith("zh") else "en"


LANGUAGE = detect_language()  # Decided once at startup.


def tr(text: str, **values: object) -> str:
    """Translate an English interface string, then fill in its {placeholders}."""
    template = ZH.get(text, text) if LANGUAGE == "zh" else text
    return template.format(**values) if values else template


def tr_n(singular: str, plural: str, count: int, **values: object) -> str:
    """Like tr(), choosing the English singular or plural form; {count} is always available."""
    return tr(singular if count == 1 else plural, count=count, **values)


ZH = {
    # Recording and transcription workers
    "Recording has not started.": "录音尚未开始。",
    "No sound was recorded; check the selected microphone.": "没有录到声音，请检查麦克风选择。",
    "Uploading…": "上传中…",
    "Transcribing…": "转写中…",
    "Gemini returned no text; check the recording and try again.": "Gemini 没有返回文字，请检查录音后重试。",
    "This recording is longer than Live can safely handle; use Standard Transcribe instead.":
        "这段录音超过 Live 安全时长，请改用普通 Transcribe。",
    "Connecting…": "连接中…",
    "Sending {percent}% · about {remaining} left": "发送中 {percent}% · 约剩 {remaining}",
    "Live closed the connection before the recording was fully sent.": "Live 连接在录音发送完之前关闭。",
    "Recording sent; waiting for the final text…": "录音已送完，等待定稿…",
    "Live returned no final text; the recording is still saved on this computer.":
        "Live 没有返回最终文字；录音仍保存在本机。",
    "Preparing…": "准备中…",
    # Vocabulary dialog
    "Vocabulary · view and edit": "个人词库 · 查看与编辑",
    "Sorted alphabetically. Typing filters the list and flags duplicates; "
    "double-click a term to edit it, or select terms and press Delete to remove them.":
        "按首字母排列。输入时即时筛选并提示重复；双击词条可修改，选中后按 Delete 删除。",
    "Type a new term and press Enter to add it": "输入新词条，回车添加",
    "Add": "添加",
    "Delete selected": "删除所选",
    "Cancel": "取消",
    "Save vocabulary": "保存词库",
    "Already in your vocabulary: {term}": "已在个人词库中：{term}",
    "No duplicate · 1 term contains this text": "没有重复 · 有 {count} 项包含这段文字",
    "No duplicate · {count} terms contain this text": "没有重复 · 有 {count} 项包含这段文字",
    "1 term": "共 {count} 项",
    "{count} terms": "共 {count} 项",
    "Added: {term} · 1 term": "已添加：{term} · 共 {count} 项",
    "Added: {term} · {count} terms": "已添加：{term} · 共 {count} 项",
    "Edit term": "修改词条",
    "Term:": "词条：",
    "Duplicate term": "重复词条",
    "Your vocabulary already has: {term}": "个人词库中已经有：{term}",
    "Vocabulary too long": "词库太长",
    "Gemini accepts at most 1,000 distinct terms.": "Gemini 最多接受 1000 个不同的词条。",
    "Vocabulary changed": "词库已更改",
    "The vocabulary file was changed by another device or program. "
    "Close and reopen the vocabulary before editing.":
        "词库文件已被其他设备或程序修改。请关闭并重新打开词库后再编辑。",
    "Save failed": "保存失败",
    # Main window
    "Records on this computer and sends only when you stop; "
    "each recording is transcribed in the background while you record the next.":
        "先在本机录音，结束后才发送；每段在后台转写，录完一段就可以接着录下一段。",
    "Settings": "基本设置",
    "Paste your Gemini API key and click Save (stored only on this computer)": "粘贴 API key，点击保存（仅保存在本机）",
    "Save key": "保存 Key",
    "Transcription mode": "转写模式",
    "VERBATIM · keeps wording and corrections": "VERBATIM · 保留原话和纠正",
    "SMART · tidies up sentences": "SMART · 自动整理语句",
    "Transcription engine": "转写引擎",
    "Transcribe Live · recommended": "Transcribe Live · 推荐",
    "Standard Transcribe · whole file": "普通 Transcribe · 整段",
    "Microphone": "麦克风",
    "View / edit vocabulary": "查看 / 编辑词库",
    "Choose vocabulary file": "选择词库文件",
    "Start recording": "开始录音",
    "Stop and submit": "结束并提交",
    "Submit, record next": "提交，录下一段",
    "Send this recording for transcription and start the next one right away": "提交当前这段去转写，并立刻开始录下一段",
    "× Discard": "× 放弃本段",
    "Throw away the current recording: it is neither saved nor sent to Gemini": "扔掉正在录的这段：不保存录音，也不发送给 Gemini",
    "Ready": "准备就绪",
    "Mic level": "麦克风声量",
    "Shown while recording": "录音时显示",
    "Picking up sound": "正在拾音",
    "Quiet / low level": "声音偏小 / 安静",
    "Recording finished": "录音已结束",
    "Transcripts": "转写结果",
    "Retry": "重试",
    "Retry now": "立即重试",
    "Copy": "复制本段",
    "Copy all": "复制全部",
    "Copy every finished transcript in recording order": "按录音顺序复制所有已完成的段落",
    "Remove selected": "移除所选",
    "Remove from the list only (or press Delete; multiple selection works); "
    "recordings and transcript files stay on this computer":
        "只从列表中移除（也可按 Delete，可多选）；录音和转写文件仍保存在本机",
    "Clear finished": "清除已完成",
    "Remove all finished recordings from the list; files stay on this computer": "从列表中移除所有已完成的段落；文件仍保存在本机",
    "Stop recording, and the text appears here once the whole recording is transcribed.":
        "结束录音并完成整段转写后，文字会出现在这里。",
    "Finished recordings and your vocabulary terms are sent to Gemini; "
    "the API key, recordings and transcripts are never committed to Git.":
        "结束录音后，音频和个人词库中的词条会发送给 Gemini；API key、录音和转写文件不会提交到 Git。",
    "{count} transcribe at once, others queue and retry if rate-limited": "最多同时转写 {count} 段，其余排队，限流时自动重试",
    "Live: up to 08:30 per recording (10-min session), then auto-submits; {parallel}.":
        "Live 每段上限 08:30（会话限 10 分钟），到时自动提交；{parallel}。",
    "Standard: up to 20 min per recording, per-minute/daily quotas apply; {parallel}.":
        "普通 Transcribe 每段最多 20 分钟，受每分钟 / 每天请求额度限制；{parallel}。",
    "{host_api} · {sample_rate} Hz · checked again when recording starts":
        "{host_api} · {sample_rate} Hz · 录音时再次确认设备可打开",
    "Verified 1 microphone that can be opened now.": "已验证 {count} 个当前可打开的麦克风。",
    "Verified {count} microphones that can be opened now.": "已验证 {count} 个当前可打开的麦克风。",
    "Could not check microphones: {error}": "无法检查麦克风：{error}",
    "API key: verified by a successful transcription": "API key：本次转写已成功验证",
    "API key: loaded from this computer; not yet verified with Google": "API key：已从本机读取；尚未向 Google 验证",
    "API key: not set": "API key：尚未设置",
    "Vocabulary file not found: {path}": "找不到词库文件：{path}",
    "Vocabulary: 1 term · {path}": "个人词库：{count} 项 · {path}",
    "Vocabulary: {count} terms · {path}": "个人词库：{count} 项 · {path}",
    "Vocabulary needs checking: {error}": "个人词库需要检查：{error}",
    "Enter the API key you want to save first.": "请先输入要保存的 API key。",
    "API key saved on this computer; the first transcription will verify it": "API key 已保存到本机，等待首次转写验证",
    "Cannot read vocabulary": "无法读取词库",
    "Vocabulary updated; it applies from the next recording": "个人词库已更新，下次录音生效",
    "Choose the vocabulary text file synced by Google Drive for desktop": "选择 Google Drive 桌面版同步的词库文本文件",
    "Text files (*.txt);;All files (*)": "文本文件 (*.txt);;所有文件 (*)",
    "Recording discarded · nothing was saved or sent": "已放弃这段录音 · 没有保存，也没有发送",
    "API key required": "需要 API key",
    "Enter and save your Gemini API key first.": "请先填写并保存 API key。",
    "Vocabulary error": "个人词库错误",
    "No usable microphone was found.": "没有找到可用的麦克风。",
    "Microphone unavailable": "麦克风无法打开",
    "This device cannot record right now: {error}": "这个设备目前不能录音：{error}",
    "Recording {number} · on this computer only, not sent yet": "正在录第 {number} 段 · 仅在本机，尚未发送",
    "Recording failed": "录音失败",
    "Recording {number} submitted · you can record the next one": "第 {number} 段已提交转写 · 可以直接录下一段",
    "30 seconds left, then it stops and submits": "还剩 30 秒，届时会自动结束录音并提交",
    "API key not set; save a key to start transcribing the queued recordings": "API key 未设置；保存 Key 后开始转写排队的录音",
    # Transcription queue
    "Recording {number} transcribed and saved on this computer": "第 {number} 段转写完成，已保存在本机",
    "Recording {number} transcribed, but saving failed": "第 {number} 段转写完成，但保存失败",
    "No text was received.": "没有收到文字。",
    "Recording {number} failed · audio kept on this computer, click “Retry”": "第 {number} 段转写失败；录音仍保存在本机，可点“重试”",
    "rate-limited": "额度限流",
    "connection lost": "连接中断",
    "Recording {number}: {reason}, retrying in {delay} s": "第 {number} 段遇到{reason}，{delay} 秒后自动重试",
    "Save failed: {error}": "保存失败：{error}",
    "Recording {number} · {time} · length {length}": "第 {number} 段 · {time} · 录音 {length}",
    "{reason} · attempt {attempt} in {wait}": "{reason} · {wait} 后第 {attempt} 次尝试",
    "× Transcription failed · click “Retry”": "× 转写失败 · 可点“重试”",
    "Queued · rate-limit cooldown {wait}": "排队中 · 限流冷却 {wait}",
    "Queued · waiting for a free slot": "排队中 · 等待空位",
    "Transcribing {running} · queued {waiting} · done {done}": "转写中 {running} · 排队 {waiting} · 完成 {done}",
    " · rate-limit cooldown {wait}": " · 限流冷却 {wait}",
    "Submit each recording when you finish it and start the next one without waiting; "
    "each is transcribed separately and shows up in the list on the left.":
        "录完一段就提交，不必等待转写，可以马上录下一段；每段独立转写，完成后在左侧列表中查看。",
    "Saved on this computer: transcripts/{name}": "已保存在本机：transcripts/{name}",
    "Recording {number} · {saved}. You can edit the text before copying.": "第 {number} 段 · {saved}。可以直接修改后复制。",
    "This recording was not transcribed; click “Retry”.\n\nReason: {error}":
        "这一段没有转写成功，可以点“重试”。\n\n失败原因：{error}",
    "Recording kept on this computer: recordings/{name}": "录音仍保存在本机：recordings/{name}",
    "\n\nLast attempt: {error}": "\n\n上次尝试：{error}",
    "This recording is being transcribed in the background and will appear here. "
    "You can go on recording the next one.{last_error}":
        "这一段正在后台转写，完成后会显示在这里。你可以继续录下一段。{last_error}",
    "Recording stored on this computer until it is transcribed, then deleted: recordings/{name}":
        "录音暂存在本机，转写完成后自动删除：recordings/{name}",
    "Removed 1 from the list · files kept on this computer": "已从列表移除 {count} 段 · 录音和转写文件仍在本机",
    "Removed {count} from the list · files kept on this computer": "已从列表移除 {count} 段 · 录音和转写文件仍在本机",
    "; 1 still transcribing, not removed": "；{count} 段还在转写，未移除",
    "; {count} still transcribing, not removed": "；{count} 段还在转写，未移除",
    "Cleared 1 finished recording · transcripts remain in transcripts/":
        "已清除 {count} 段已完成的记录 · 转写文件仍在 transcripts/",
    "Cleared {count} finished recordings · transcripts remain in transcripts/":
        "已清除 {count} 段已完成的记录 · 转写文件仍在 transcripts/",
    "Copied to the clipboard": "已复制本段到剪贴板",
    "Copied 1 transcript to the clipboard": "已按录音顺序复制 {count} 段到剪贴板",
    "Copied {count} transcripts to the clipboard in recording order": "已按录音顺序复制 {count} 段到剪贴板",
    "1 recording is still transcribing; the window closes when it is done…": "还有 {count} 段在转写，完成后自动关闭窗口…",
    "{count} recordings are still transcribing; the window closes when they are done…":
        "还有 {count} 段在转写，完成后自动关闭窗口…",
}
