"""Launch without a console; show useful errors instead of silently disappearing."""

import ctypes
import sys
import traceback
from pathlib import Path

project = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project))

try:
    from gemini_live_dictation.gui import main

    main()
except Exception:
    log = project / ".runtime" / "launch-error.log"
    try:
        log.parent.mkdir(exist_ok=True)
        log.write_text(traceback.format_exc(), encoding="utf-8")
        detail = f"详细信息：{log}"
    except OSError:
        detail = "无法写入错误日志，请检查项目文件夹是否可写。"
    ctypes.windll.user32.MessageBoxW(
        None,
        "程序未能启动。请双击 Install Dictation.cmd 修复安装。\n\n" + detail,
        "Gemini Dictation",
        0x10,
    )
