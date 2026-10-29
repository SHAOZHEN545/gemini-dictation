"""Launch without a console; show useful errors instead of silently disappearing."""

import ctypes
import os
import sys
import traceback
from pathlib import Path

project = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project))


def chinese() -> bool:
    """Match the app's language without importing it, since the import itself may be what failed."""
    override = os.environ.get("GEMINI_DICTATION_LANG", "").strip().lower()[:2]
    if override in ("en", "zh"):
        return override == "zh"
    return (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x04  # LANG_CHINESE


try:
    from gemini_live_dictation.gui import main

    main()
except Exception:
    zh = chinese()
    log = project / ".runtime" / "launch-error.log"
    try:
        log.parent.mkdir(exist_ok=True)
        log.write_text(traceback.format_exc(), encoding="utf-8")
        detail = f"详细信息：{log}" if zh else f"Details: {log}"
    except OSError:
        detail = (
            "无法写入错误日志，请检查项目文件夹是否可写。"
            if zh
            else "Could not write the error log; check that the project folder is writable."
        )
    message = (
        "程序未能启动。请双击 Install Dictation.cmd 修复安装。"
        if zh
        else "The app could not start. Double-click Install Dictation.cmd to repair the installation."
    )
    ctypes.windll.user32.MessageBoxW(None, message + "\n\n" + detail, "Gemini Dictation", 0x10)
