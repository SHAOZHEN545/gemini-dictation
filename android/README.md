# Android 语音输入法（首个测试版）

这是 Android 系统输入法，不是主屏幕网页快捷方式。点击麦克风开始录音，再点一次结束；整段录音在本机暂存，完成后发送给 Gemini Live，最终文字写入当前输入框。切换输入框或关闭键盘会取消当前录音或转写。密码输入框禁用语音。

## 构建与安装

推送本目录后，GitHub Actions 的 **Build Android keyboard** 会构建可安装的调试 APK，并在该次运行的 Artifacts 中提供 `gemini-dictation-keyboard-debug`。这个 APK 用于个人测试；尚未制作正式签名版本，也尚未在实体手机上验证。

若在本机开发，安装 Android SDK 35、JDK 17 和 Gradle 8.9，在 `android/` 运行 `gradle :app:assembleDebug`。也可以先运行 `gradle :wrapper --gradle-version 8.9` 生成 Gradle Wrapper，再用 Android Studio 打开项目。项目使用 Android Gradle Plugin 8.7.0。APK 在 `app/build/outputs/apk/debug/`。

安装后打开“Gemini 语音输入”：

1. 授权麦克风，从 Bitwarden 粘贴自己的 Gemini API Key 并保存。Key 经 Android Keystore 加密后仅留在手机，不会上传 Drive。
2. 可以先把电脑上的 `config/vocabulary.txt` 上传到 Google Drive。点“选择已有词库文件”或“在 Google Drive 新建词库文件”，在系统文件选择器里进入 Google Drive 并选择目标位置和 `.txt` 文件。应用持有该文件的长期读写授权，不需要访问整个网盘。
3. 编辑词条并点“保存词库到 Drive”。每行一个词条；`#` 开头的行是注释。如果别的设备改过文件，保存会要求你先重新读取，避免覆盖。每次结束录音后，输入法会重新读取这份文件。
4. 点“启用 Gemini 语音输入”，再点“选择当前输入法”。在任意普通文本框中点击麦克风使用。需要普通键盘时点地球图标切回三星键盘。

录音和转写文字不写入手机文件。词库只通过你选中的文件读写；如果 Drive 暂时不可用，转写会显示读取失败，重新联网后再试。当前语音模式固定为 `VERBATIM`，单段最长 8 分 30 秒。

网页版和 Windows 桌面版尚未接入这份 Drive 文件；网页版目前仍使用各浏览器的本机词库。网页接入相同文件需要另外配置 Web OAuth 与文件选择流程。
