@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo The project environment is missing. See README.md for first-time setup.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m gemini_live_dictation.gui
