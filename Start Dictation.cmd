@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    call "%~dp0Install Dictation.cmd"
    exit /b 0
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0scripts\launch-desktop.pyw"
