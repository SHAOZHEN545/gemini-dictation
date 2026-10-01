# Gemini Live Dictation

## 新电脑：双击安装与启动

Windows 10 / 11 的 Intel / AMD 64 位电脑可以直接使用：

1. 从 GitHub 下载仓库 ZIP，**先完整解压**到一个准备长期保留的文件夹；如果已经克隆仓库，直接使用该文件夹。
2. 双击 **`Install Dictation.cmd`**。安装窗口会显示进度，自动下载独立的 Python 运行环境、安装桌面和音频组件、创建桌面快捷方式，并打开程序。首次安装需要联网，可能需要几分钟；无需输入命令、预装 Python 或使用管理员权限。
3. 在程序里粘贴自己的 **Gemini API Key**，点击“保存 Key”。新电脑需要重新填写，GitHub 仓库不会携带其他电脑的 Key。
4. 如果要使用云端词库，先安装并登录 **Google Drive 桌面版**，再点程序里的“选择词库文件”，选择同步到这台电脑的那份 `.txt`。也可以先使用本机词库开始录音。

以后双击桌面的 **Gemini Dictation** 或项目里的 **`Start Dictation.cmd`** 即可。首次直接点“Start”时，如果还没有运行环境，也会打开安装窗口。更新仓库后，可以再次双击“Install”更新依赖；已有 Key、词库、录音和转写文件会保留。

请保留项目文件夹：快捷方式指向其中的程序，移动文件夹后需要在新位置重新双击安装。安装工具、下载缓存放在 `.runtime/`，运行环境放在 `.venv/`，均不会提交到 Git。安装失败会显示原因，并保存 `.runtime/install.log`；启动时发生程序错误会显示提示并保存 `.runtime/launch-error.log`。

安装流程使用固定版本、校验 SHA-256 的 [uv 官方发布文件](https://github.com/astral-sh/uv/releases/tag/0.12.19)，由 uv 下载本项目独立使用的 Python，不修改系统 PATH。运行环境管理方式见 [uv 文档](https://docs.astral.sh/uv/guides/install-python/)。首次安装需能访问 GitHub 和 PyPI。

手机上请按下方 Android 说明安装 APK；仅在电脑下载仓库不会安装手机输入法。网页版也可直接在浏览器使用，无需安装桌面程序。

Windows 窗口、任务栏、安装窗口和桌面快捷方式，以及 Android 应用和网页主屏幕图标统一使用可达鸭。更新后重启桌面程序，并再次运行 `Install Dictation.cmd` 刷新桌面快捷方式；如任务栏固定图标仍显示旧图标，取消固定后从新版程序重新固定。Android 需安装新版 APK；网页需发布新版，已添加到手机主屏幕的旧图标如未刷新，可删除快捷方式后重新添加。

原始图标保存在 `gemini_live_dictation/assets/app-icon.png`，Windows ICO、Android 各密度与自适应图标、网页图标均由 `scripts/generate-icons.py` 生成。修改原图后，在装有 Pillow 的开发环境运行该脚本即可更新；应用运行时不需要 Pillow。

## Android 语音输入法（测试版）

`android/` 中新增 Android 系统输入法：可以在三星手机的其他 App 输入框里点击麦克风口述，转写结果直接输入。词库通过 Android 系统文件选择器授权读写 Google Drive 中的一份 `.txt` 文件；Gemini API Key 从 Bitwarden 粘贴后加密保存在手机本地。安装和使用步骤见 [Android 说明](android/README.md)。

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

推荐直接按上面的“双击安装与启动”操作。如果已经准备好 Python 虚拟环境，开发时也可以手动安装桌面版：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[gui]"
```

以后可以直接双击项目里的 `Start Dictation.cmd`，或运行：

```powershell
.\.venv\Scripts\gemini-live-gui.exe
```

如果已有 `.env`，界面会显示“已从本机读取”；成功转写后才显示“本次转写已成功验证”。
也可以在界面中粘贴新 key 并点击“保存 Key”。点击“查看 / 编辑词库”可以检查并修改
当前选中的 `.txt` 词库文件：词条按首字母竖向排列，输入新词时会即时筛选并提示是否重复
（不区分大小写），双击可修改，选中后按 Delete 删除。转写时，音频和个人词库中的词条会发送给 Gemini。
点击“选择词库文件”可以改用 Google Drive 桌面版同步到电脑的那份 `.txt` 文件。保存前会检查文件是否被其他设备改过；每次开始录音都会重新读取。桌面版使用 Drive 桌面版的同步文件路径，需在电脑上安装并登录 Google Drive 桌面版。词库文件不会随网站发布。
麦克风列表只显示当前可打开的设备，并将重复的驱动入口合并。
界面默认选择 `VERBATIM`，因为它会保留口述中的纠正；需要更整洁的文字时可切换到 `SMART`。
Live 单次会话的官方上限为 10 分钟，界面把录音限制在 8 分 30 秒并自动停止，给连接和
收尾留出余量。超过这个长度可分段录制；也可以切换到普通 `gemini-3.5-transcribe`，
本程序最多录 20 分钟，但普通版的请求受账号额度限制。Live 虽然在用户当前的免费额度
截图中显示不限制每分钟和每天请求数，仍受每分钟 token 和单次会话时长限制。

## 网页版（手机、平板、任何浏览器）

`web/` 是同一套流程的网页版，不需要安装：先在设备上录音，结束后整段交给 Live 转写，
可以连续录多段，排队、限流重试和桌面版一样。网页是纯静态文件，推送到 `main` 后由
GitHub Actions 发布到 GitHub Pages。点击“查看 / 编辑词库”可在网页编辑个人词库。未连接 Google Drive 时，词库保存在当前浏览器；此前网页版缓存的词条会在首次打开新版时迁移到本机词库。连接 Drive 后，网页会在每次录音或导入音频前读取所选云端 `.txt` 文件，并可把编辑结果保存回同一文件。浏览器每次重新打开后需要重新授权；授权失败时不会用旧词库静默转写。

### 网页连接 Google Drive 词库

1. 在 [Google Cloud Console](https://console.cloud.google.com/) 建立项目，启用 **Google Drive API** 和 **Google Picker API**，配置 OAuth 同意屏幕；如果应用仍处于测试状态，把自己的 Google 账号加入测试用户。
2. 建立 **Web application** OAuth 客户端，把网页的来源（例如 `https://<用户名>.github.io`，本地预览还需 `http://localhost:8765`）加入 Authorized JavaScript origins。建立 API key，并记录 Cloud **项目编号**。若限制 API key 的网站来源，按 Google Picker 文档同时允许网页来源和 `https://docs.google.com/*`；若限制 API，允许 Picker API 和 Drive API。
3. 打开网页的“查看 / 编辑词库 → 连接 Google Drive 词库”，填入客户端 ID、API key 和项目编号，点击“选择 Drive 词库”，登录后选中安卓使用的同一份 `.txt` 文件。授权范围是 `drive.file`。这些公开的网页配置保存在当前浏览器；访问令牌只放在页面内存中，不上传到本站服务器。
4. 如已有浏览器本地词库，先把词条复制到 Drive 文件里，再切换。切换不会自动合并两份词库。保存时会先对比云端文件，发现其他设备改过会要求重新读取，避免直接覆盖。Google Drive API 的读取与写入不是一个原子事务，极短时间内的同时写入仍可能冲突。

Google 官方说明：[网页授权](https://developers.google.com/identity/oauth2/web/guides/use-token-model)、[Google Picker](https://developers.google.com/workspace/drive/picker/guides/web-picker)、[Drive 文件更新](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/update)。

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
- `config/vocabulary.txt` is ignored by Git and no longer included in the web deployment.
- The web vocabulary is stored locally in each browser unless a Drive file is selected. Earlier commits and deployments may still contain the old vocabulary; removing it from the current site does not erase Git history or caches.
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

Add one term or phrase per line. The file stays local and is ignored by Git. Gemini accepts
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

1. 在真实 Windows 电脑上试一段短录音，确认麦克风和转写正常。
2. 在 Android 实体手机上验证安装、输入法切换、语音输入和 Drive 词库读写。
3. 调整个人词库，并在多台设备上检查同步效果。
