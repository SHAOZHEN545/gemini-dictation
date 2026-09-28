# Gemini Live Dictation

## 桌面版（Windows）

这个项目有一个本地桌面界面。打开后选择 `VERBATIM` 或 `SMART`、选择麦克风，
点“开始录音”说话，再点“结束录音”。录音期间只在本机缓存，不会发送给 Gemini，
也不会提前显示转写。默认选择 `gemini-3.5-transcribe-live`：结束录音后，程序关闭 Live 的
自动断句，把整段录音作为“一句话”以约 4 倍速发送（Live 自身约以 4 倍速处理；发得更快会让未处理的音频积压，超过约
80 秒时服务器会断开连接），Live 结合整段上下文一次给出最终文字。
这样不会在每次停顿处单独定稿、断章取义；实测等待时间约为录音时长的四分之一（1 分钟录音约
等 15–20 秒）。界面中的声量条仅显示本机麦克风的输入电平。
录完一段不必等转写：点“结束并提交”后可以马上录下一段，录音时也可以点“提交，录下一段”一步
完成。录得不满意时点“× 放弃本段”：这段录音直接丢弃，不保存到本机，也不发送给 Gemini。
每段录音是独立的转写任务，在左侧列表里显示排队、发送进度、完成或重试倒计时；最多同时
转写 3 段，其余按录音顺序排队。Live 的并发会话额度没有公开文档：用本机 key 同时开 10 个会话，
有 5–8 个被接受，其余立即返回 `1011 … exceeded your current quota`，稍等即可恢复。因此遇到
额度限流时，程序暂停启动新任务，按服务器提示（没有提示时依次等 10 / 30 / 60 秒）自动重试，
最多 4 次；连接中断也会自动重试，API key 无效等无法靠重试解决的错误会直接标为失败，可手动“重试”。
选中任一段可查看、修改和“复制本段”；“复制全部”按录音顺序合并所有已完成的段落。
列表太长时，可以多选后点“移除所选”（或按 Delete），或点“清除已完成”；这只是从列表中移除，
`transcripts/` 里的文件不会删除，在窗口里改过的文字会先写回转写文件。
正在转写或排队的段落不能移除。
每段文字也会保存在本机的 `transcripts/`。
录音在转写完成前暂存在本机的 `recordings/`；文字写入 `transcripts/` 后，录音自动删除。
转写失败的录音会保留，以便重试。两个文件夹都被 Git 忽略。

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
`config/vocabulary.txt`：词条按首字母竖向排列，输入新词时会即时筛选并提示是否重复
（不区分大小写），双击可修改，选中后按 Delete 删除。转写时，音频和词表会发送给 Gemini。
词表文件纳入 Git：改完后提交并推送，网页版会自动用上新词表。
麦克风列表只显示当前可打开的设备，并将重复的驱动入口合并。
界面默认选择 `VERBATIM`，因为它会保留口述中的纠正；需要更整洁的文字时可切换到 `SMART`。
Live 单次会话的官方上限为 10 分钟，界面把录音限制在 8 分 30 秒并自动停止，给连接和
收尾留出余量。超过这个长度可分段录制；也可以切换到普通 `gemini-3.5-transcribe`，
本程序最多录 20 分钟，但普通版的请求受账号额度限制。Live 虽然在用户当前的免费额度
截图中显示不限制每分钟和每天请求数，仍受每分钟 token 和单次会话时长限制。

## 网页版（手机、平板、任何浏览器）

`web/` 是同一套流程的网页版，不需要安装：先在设备上录音，结束后整段交给 Live 转写，
可以连续录多段，排队、限流重试和桌面版一样。网页是纯静态文件，推送到 `main` 后由
GitHub Actions 发布到 GitHub Pages，`config/vocabulary.txt` 会一起发布，所以每台设备
自动用同一份词表（网页只能查看，编辑请用桌面版）。

- **API key**：网页代码里没有 key。每台设备第一次打开时从 Bitwarden 粘贴一次，只存在
  那个浏览器的本地存储里；音频从设备直接发给 Google，中间没有别的服务器。
- **录音**：只在转写完成前暂存在浏览器（IndexedDB），转写成功后自动删除；失败的段落保留
  录音以便重试，点“移除”即删除。放弃的段落不保存。
- **文字**：保留在这个浏览器里，可以修改、复制本段或复制全部，点“清除已完成”删除。
- 也可以用“导入音频”转写设备上已有的录音文件（每段不超过 08:30）。
- 转写时请让页面保持在前台：手机锁屏或切换应用时，浏览器可能暂停录音和发送。
- 在 iPhone / iPad 的 Safari 里选“分享 → 添加到主屏幕”，之后可以像 App 一样打开。

首次发布需要在 GitHub 上做两件事：仓库设为 public（免费账号的 Pages 要求），并在
Settings → Pages → Build and deployment 中把 Source 设为 **GitHub Actions**。网址是
`https://<用户名>.github.io/gemini-dictation/`。

本地预览：在项目根目录运行 `.\.venv\Scripts\python.exe -m http.server 8765 --bind 127.0.0.1`，
然后打开 `http://localhost:8765/web/`（localhost 可以使用麦克风）。

## 命令行 Live 实验

The separate command-line tool streams microphone audio to `gemini-3.5-transcribe-live`.
It streams 16 kHz mono microphone audio to Gemini and prints both interim and finalized
transcription. It can also stream a saved audio file at playback speed. Automatic language
detection supports Chinese-English code switching.

## Privacy defaults

- The API key is read from `GEMINI_API_KEY`, optionally via an ignored `.env` file.
- `transcripts/` and `recordings/` are ignored by Git.
- `config/vocabulary.txt` is tracked (and published with the web version) so every device shares it.
- No API key, audio recording, or transcript is included in source control.

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

If `config/vocabulary.txt` does not exist yet, create it from the example:

```powershell
Copy-Item config\vocabulary.example.txt config\vocabulary.txt
```

Add one term or phrase per line. The file is tracked by Git and published with the web version. Gemini accepts
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
