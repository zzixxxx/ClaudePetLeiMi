# ClaudePetLeiMi 一键安装脚本
# 用法 (PowerShell):
#   irm https://raw.githubusercontent.com/zzixxxx/ClaudePetLeiMi/main/install.ps1 | iex
$ErrorActionPreference = "Stop"

$repo = "https://github.com/zzixxxx/ClaudePetLeiMi"
$dest = Join-Path $env:LOCALAPPDATA "ClaudePetLeiMi"

Write-Host "== ClaudePetLeiMi 安装 ==" -ForegroundColor Cyan

# 1. Python 检查 (硬依赖: 桌宠与 hooks 都跑在 Python 上)
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "未找到 Python。请先安装 Python 3.10+ (https://www.python.org/downloads/)，勾选 'Add to PATH' 后重新运行本脚本。" -ForegroundColor Red
    return
}
$pyVer = & python -c "import sys; print(sys.version_info >= (3, 10))"
if ($pyVer.Trim() -ne "True") {
    Write-Host "Python 版本过低，需要 3.10+。" -ForegroundColor Red
    return
}

# 2. 下载并解压最新代码
Write-Host "下载 $repo ..."
$zip = Join-Path $env:TEMP "ClaudePetLeiMi.zip"
$extract = Join-Path $env:TEMP "ClaudePetLeiMi_extract"
Invoke-WebRequest "$repo/archive/refs/heads/main.zip" -OutFile $zip -UseBasicParsing
if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
Expand-Archive $zip -DestinationPath $extract -Force
New-Item -ItemType Directory -Force $dest | Out-Null
Copy-Item (Join-Path $extract "ClaudePetLeiMi-main\*") $dest -Recurse -Force
Remove-Item $zip -Force; Remove-Item $extract -Recurse -Force

# 3. 依赖
Write-Host "安装依赖 (Pillow / pystray) ..."
& python -m pip install --quiet --disable-pip-version-warning pillow pystray

# 4. 合并 hooks / statusLine 到 ~/.claude/settings.json (幂等)
& python (Join-Path $dest "install_hooks.py")

# 5. 开机自启 + 桌面快捷方式
$pythonw = Join-Path (Split-Path (Get-Command python).Source) "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = (Get-Command python).Source }
$ws = New-Object -ComObject WScript.Shell
$targets = @(
    (Join-Path ([Environment]::GetFolderPath("Startup")) "ClaudePetLeiMi.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "ClaudePetLeiMi.lnk")
)
foreach ($lnkPath in $targets) {
    $lnk = $ws.CreateShortcut($lnkPath)
    $lnk.TargetPath = $pythonw
    $lnk.Arguments = "`"$(Join-Path $dest 'claude_pet.pyw')`""
    $lnk.WorkingDirectory = $dest
    $lnk.Save()
}

# 6. 启动 (单实例, 重复启动自动顶替)
Start-Process $pythonw "`"$(Join-Path $dest 'claude_pet.pyw')`""

Write-Host ""
Write-Host "安装完成！桌宠已启动（屏幕右下角）。" -ForegroundColor Green
Write-Host "  安装目录: $dest"
Write-Host "  已配置:  Claude Code hooks + statusLine、开机自启、桌面快捷方式"
Write-Host "  注意:    正在运行的 Claude Code 会话需重启才会驱动桌宠"
Write-Host "  卸载:    删除安装目录与两个快捷方式，并从 ~/.claude/settings.json 移除 cc_pet_hook 相关 hooks"
