# Saved as UTF-8 with BOM for Windows PowerShell 5.1.
[CmdletBinding()]
param(
    [switch]$Headless,
    [switch]$NoLaunch,
    [switch]$NoShortcuts
)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtimeRoot = Join-Path $projectRoot '.runtime'
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$pythonwExe = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$logPath = Join-Path $runtimeRoot 'install.log'
$form = $null
$installLock = $null

function Write-InstallLog([string]$Message) {
    [IO.File]::AppendAllText($logPath, $Message + [Environment]::NewLine, [Text.Encoding]::UTF8)
}

function Update-Progress([string]$Message) {
    Write-InstallLog $Message
    if ($Headless) { Write-Host $Message }
    else {
        $statusLabel.Text = $Message
        [Windows.Forms.Application]::DoEvents()
    }
}

function Wait-ForUI {
    if (-not $Headless) { [Windows.Forms.Application]::DoEvents() }
    Start-Sleep -Milliseconds 100
}

# ProcessStartInfo.Arguments uses Windows quoting, not shell or PowerShell syntax.
function Quote-NativeArgument([string]$Value) {
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Invoke-InstallerProcess([string]$Executable, [string[]]$Arguments) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $Executable
    $info.Arguments = ($Arguments | ForEach-Object { Quote-NativeArgument $_ }) -join ' '
    $info.WorkingDirectory = $projectRoot
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $deadline = [DateTime]::UtcNow.AddMinutes(15)
        while (-not $process.HasExited) {
            if ([DateTime]::UtcNow -gt $deadline) {
                $process.Kill()
                throw '下载或安装超时。请检查网络后重新双击安装文件。'
            }
            Wait-ForUI
        }
        Write-InstallLog $stdout.GetAwaiter().GetResult()
        Write-InstallLog $stderr.GetAwaiter().GetResult()
        return $process.ExitCode
    }
    finally { $process.Dispose() }
}

function Download-InstallerFile([string]$Url, [string]$Destination) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $client = New-Object Net.WebClient
    try {
        $client.Headers.Add('User-Agent', 'Gemini-Dictation-Installer')
        $task = $client.DownloadFileTaskAsync([Uri]$Url, $Destination)
        $deadline = [DateTime]::UtcNow.AddMinutes(5)
        while (-not $task.IsCompleted) {
            if ([DateTime]::UtcNow -gt $deadline) {
                $client.CancelAsync()
                throw '下载超时。请检查网络后重新双击安装文件。'
            }
            Wait-ForUI
        }
        $task.GetAwaiter().GetResult()
    }
    finally { $client.Dispose() }
}

try {
    [void][IO.Directory]::CreateDirectory($runtimeRoot)
    try {
        $installLock = [IO.File]::Open((Join-Path $runtimeRoot 'install.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    }
    catch { throw '另一个安装窗口正在运行。请等待它完成后再试。' }
    [IO.File]::WriteAllText($logPath, "Gemini Dictation installation`r`n", [Text.Encoding]::UTF8)

    if (-not $Headless) {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        [Windows.Forms.Application]::EnableVisualStyles()
        $form = New-Object Windows.Forms.Form
        $form.Text = '安装 Gemini Dictation'
        $form.ClientSize = New-Object Drawing.Size(560, 215)
        $form.StartPosition = 'CenterScreen'
        $form.FormBorderStyle = 'FixedDialog'
        $form.MaximizeBox = $false
        $form.ControlBox = $false
        $form.Font = New-Object Drawing.Font('Microsoft YaHei UI', 10)
        $intro = New-Object Windows.Forms.Label
        $intro.SetBounds(24, 18, 512, 52)
        $intro.Text = "首次安装需要联网下载运行环境。`r`n请稍候，无需输入命令或安装其他软件。"
        $statusLabel = New-Object Windows.Forms.Label
        $statusLabel.SetBounds(24, 83, 512, 48)
        $progressBar = New-Object Windows.Forms.ProgressBar
        $progressBar.SetBounds(24, 145, 512, 20)
        $progressBar.Style = 'Marquee'
        $progressBar.MarqueeAnimationSpeed = 25
        $form.Controls.AddRange(@($intro, $statusLabel, $progressBar))
        $form.Show()
        [Windows.Forms.Application]::DoEvents()
    }

    if (-not [Environment]::Is64BitOperatingSystem -or
        $env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') {
        throw '此安装入口支持 Windows 10 / 11 的 Intel 或 AMD 64 位电脑。其他设备可使用网页版。'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'pyproject.toml'))) {
        throw '请先完整解压项目，再从项目文件夹双击安装文件。'
    }

    Update-Progress '1 / 4：准备安装工具…'
    # Pin the release and verify its published SHA-256 before executing it.
    $uvVersion = '0.12.19'
    $uvDigest = '6dbb02d79e419522f1c500f0adb1cddcff0cda7d59b0d66ea7f5e3b4a1b2f5f0'
    $uvDirectory = Join-Path $runtimeRoot "uv-$uvVersion"
    $uvExe = Join-Path $uvDirectory 'uv.exe'
    if (-not (Test-Path -LiteralPath $uvExe)) {
        $archive = Join-Path $runtimeRoot "uv-$uvVersion.zip"
        Download-InstallerFile "https://github.com/astral-sh/uv/releases/download/$uvVersion/uv-x86_64-pc-windows-msvc.zip" $archive
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $uvDigest) {
            throw '下载文件的安全校验失败。请重新安装；程序未执行该文件。'
        }
        Expand-Archive -LiteralPath $archive -DestinationPath $uvDirectory -Force
    }
    # Keep Python and caches local. Never add anything to PATH or change global Python.
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $runtimeRoot 'python'
    $env:UV_CACHE_DIR = Join-Path $runtimeRoot 'cache'
    $env:UV_PYTHON_NO_REGISTRY = '1'
    $env:UV_NO_PROGRESS = '1'
    $env:UV_NO_CONFIG = '1'

    Update-Progress '2 / 4：准备独立的 Python 运行环境…'
    $healthy = $false
    if ((Test-Path -LiteralPath $pythonExe) -and (Test-Path -LiteralPath $pythonwExe)) {
        try {
            $healthy = (Invoke-InstallerProcess $pythonExe @('-c', 'import sys,struct; sys.exit(0 if (3,11) <= sys.version_info[:2] < (3,15) and struct.calcsize("P") == 8 else 1)')) -eq 0
        }
        catch { Write-InstallLog $_.Exception.Message }
    }
    if (-not $healthy) {
        $venvDirectory = Join-Path $projectRoot '.venv'
        if (Test-Path -LiteralPath $venvDirectory) {
            # Preserve an old/copied environment instead of deleting user files.
            $backup = Join-Path $runtimeRoot ('old-venv-' + [Guid]::NewGuid().ToString('N'))
            if (-not [IO.Path]::GetFullPath($backup).StartsWith($runtimeRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
                throw '运行环境备份位置无效。'
            }
            Move-Item -LiteralPath $venvDirectory -Destination $backup
        }
        if ((Invoke-InstallerProcess $uvExe @('venv', '--managed-python', '--python', '3.13', $venvDirectory)) -ne 0) {
            throw '无法准备 Python。请确认能访问 GitHub，然后重新安装。'
        }
    }

    Update-Progress '3 / 4：安装桌面程序和音频组件（可能需要几分钟）…'
    if ((Invoke-InstallerProcess $uvExe @('pip', 'install', '--python', $pythonExe, '--upgrade', '-e', '.[gui]')) -ne 0) {
        throw '组件安装失败。请确认能访问 GitHub 和 PyPI，检查网络后重新安装。'
    }

    Update-Progress '4 / 4：检查程序并创建快捷方式…'
    if ((Invoke-InstallerProcess $pythonExe @('-c', 'import gemini_live_dictation.gui, av, sounddevice; from PySide6.QtWidgets import QApplication')) -ne 0) {
        throw '程序检查未通过。请重新安装，或查看安装日志。'
    }
    if (-not $NoShortcuts) {
        $desktop = [Environment]::GetFolderPath('DesktopDirectory')
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut((Join-Path $desktop 'Gemini Dictation.lnk'))
        $shortcut.TargetPath = $pythonwExe
        $shortcut.Arguments = Quote-NativeArgument (Join-Path $projectRoot 'scripts\launch-desktop.pyw')
        $shortcut.WorkingDirectory = $projectRoot
        $shortcut.Description = 'Gemini 语音转写'
        $shortcut.Save()
    }
    Update-Progress '安装完成。首次打开后，请保存 API Key 并选择词库文件。'
    if (-not $NoLaunch) {
        Start-Process -FilePath $pythonwExe -ArgumentList (Quote-NativeArgument (Join-Path $projectRoot 'scripts\launch-desktop.pyw')) -WorkingDirectory $projectRoot -WindowStyle Hidden
    }
    if (-not $Headless) {
        $form.Hide()
        [void][Windows.Forms.MessageBox]::Show('安装完成。以后双击桌面上的 Gemini Dictation 即可打开。请保留当前项目文件夹。', 'Gemini Dictation', 'OK', 'Information')
    }
    exit 0
}
catch {
    $message = $_.Exception.Message
    if (Test-Path -LiteralPath $runtimeRoot) {
        try { Write-InstallLog $message } catch { }
    }
    if ($Headless) { [Console]::Error.WriteLine($message + "`n日志：" + $logPath) }
    else {
        Add-Type -AssemblyName System.Windows.Forms
        if ($null -ne $form) { $form.Hide() }
        [void][Windows.Forms.MessageBox]::Show($message + "`r`n`r`n无需输入命令，修复后重新双击 Install Dictation.cmd 即可。`r`n日志：" + $logPath, 'Gemini Dictation 安装未完成', 'OK', 'Error')
    }
    exit 1
}
finally {
    if ($null -ne $installLock) { $installLock.Dispose() }
    if ($null -ne $form) { $form.Dispose() }
}
