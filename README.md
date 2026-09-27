# Gemini Live Dictation

## 桌面版（Windows）

这个项目有一个本地桌面界面。打开后选择 `VERBATIM` 或 `SMART`、选择麦克风，
点“开始录音”说话，再点“结束录音”。录音期间只在本机缓存，不会发送给 Gemini，
也不会提前显示转写。结束后，程序把整段录音一次提交给 `gemini-3.5-transcribe`。
最终文字会出现在窗口里，可以一键复制，也会保存在本机的 `transcripts/`。
录音文件保存在本机的 `recordings/`，两个文件夹都被 Git 忽略。

在项目文件夹的 PowerShell 中首次安装桌面版：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[gui]"
```

以后可以直接双击项目里的 `Start Dictation.cmd`，或运行：

```powershell
.\.venv\Scripts\gemini-live-gui.exe
```

如果已有 `.env`，界面会显示“已从本机读取”；成功转写后才显示“本次转写已成功验证”。
也可以在界面中粘贴新 key 并点击“保存 Key”。点击“查看 / 编辑词表”可以检查并修改
`config/vocabulary.txt`。转写时，音频和词表会发送给 Gemini；这些文件不会提交到 Git。
麦克风列表只显示当前可打开的设备，并将重复的驱动入口合并。
界面默认选择 `VERBATIM`，因为它会保留口述中的纠正；需要更整洁的文字时可切换到 `SMART`。
单次录音最长 20 分钟。普通 Transcribe 的请求受账号额度限制。

GitHub 可以保存这套程序的代码。浏览器直接使用的公开网页需要另外设计密钥服务，
所以目前的桌面版在本机使用。

## 命令行 Live 实验

The separate command-line tool streams microphone audio to `gemini-3.5-transcribe-live`.
It streams 16 kHz mono microphone audio to Gemini and prints both interim and finalized
transcription. It can also stream a saved audio file at playback speed. Automatic language
detection supports Chinese-English code switching.

## Privacy defaults

- The API key is read from `GEMINI_API_KEY`, optionally via an ignored `.env` file.
- `config/vocabulary.txt`, `transcripts/`, and `recordings/` are ignored by Git.
- No API key, audio recording, transcript, or personal vocabulary is included in source control.

## Setup (Windows PowerShell)

Use the same Python 3.13 installation that you normally use. This project creates a separate
environment, so it does not change its global NumPy/SciPy packages:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
Copy-Item .env.example .env
```

Put your Gemini API key in `.env`. Do not paste it into a terminal transcript, source file, or
commit.

If PowerShell blocks activation, run this once for the current window only:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

## Vocabulary

Create a private vocabulary file from the example:

```powershell
Copy-Item config\vocabulary.example.txt config\vocabulary.txt
```

Add one term or phrase per line. The local file is intentionally ignored by Git. Gemini accepts
up to 1,000 terms; Google recommends keeping the active list to roughly 100 or fewer.

## Run

```powershell
gemini-live-dictation --vocabulary config\vocabulary.txt --mode SMART --output transcripts\session.txt
```

Press `Ctrl+C` to stop. `SMART` removes fillers and applies readable punctuation; use
`--mode VERBATIM` for a literal test transcript. To inspect available microphones:

```powershell
gemini-live-dictation --list-devices
```

For a 15-second connection test, add `--duration 15`. Every run stops automatically after
at most 540 seconds, below the Live Transcription 10-minute session limit. Run the command
again to start a new session; the `--output` file can be reused to append final segments.

To compare Live with a previous transcription of a saved recording:

```powershell
gemini-live-dictation --file "C:\Users\ustcy\Documents\test.m4a" --vocabulary config\vocabulary.txt --mode SMART --output transcripts\live-test.txt
```

The file is decoded locally and streamed at its original speed. Only final text is saved;
the audio file is never copied into this repository.

## Next steps

1. Try a short microphone dictation in the desktop app and check the selected input device.
2. Tune the private vocabulary list for terms that are still misheard.
3. Consider packaging the desktop app as a Windows executable after the interaction feels right.
