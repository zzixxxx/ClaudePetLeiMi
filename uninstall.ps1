# ClaudePetLeiMi 一键卸载
# 用法 (PowerShell):
#   irm https://raw.githubusercontent.com/zzixxxx/ClaudePetLeiMi/main/uninstall.ps1 | iex
$ErrorActionPreference = "SilentlyContinue"

# 从磁盘运行(菜单"卸载"入口)时卸载自身所在目录; irm|iex 时用默认安装目录
$dest = if ($PSScriptRoot) { $PSScriptRoot } else { Join-Path $env:LOCALAPPDATA "ClaudePetLeiMi" }
Write-Host "== ClaudePetLeiMi 卸载 ==" -ForegroundColor Cyan

# 1. 停止桌宠 (按 pet.pid 精确停, 不误伤其他 python 程序)
$pidFile = Join-Path $dest "pet.pid"
if (Test-Path $pidFile) {
    $petPid = (Get-Content $pidFile).Trim()
    $proc = Get-Process -Id $petPid -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -like "python*") {
        Stop-Process -Id $petPid -Force
        Write-Host "已停止桌宠 (PID $petPid)"
    }
}

# 2. 从 ~/.claude/settings.json 移除 hooks / statusLine
$hooksScript = Join-Path $dest "install_hooks.py"
if (Test-Path $hooksScript) {
    & python $hooksScript --remove
}

# 3. 删除快捷方式
foreach ($lnk in @(
    (Join-Path ([Environment]::GetFolderPath("Startup")) "ClaudePetLeiMi.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "ClaudePetLeiMi.lnk")
)) {
    if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Host "已删除 $lnk" }
}

# 4. 删除安装目录与状态文件 (开发目录含 .git 时保留文件, 只清配置)
Start-Sleep -Seconds 1
if (Test-Path (Join-Path $dest ".git")) {
    Write-Host "检测到开发目录(.git), 跳过文件删除" -ForegroundColor Yellow
} else {
    Remove-Item $dest -Recurse -Force
}
foreach ($f in @("cc-pet-state.json", "cc-pet-sessions.json", "cc-pet-usage.json", "cc-pet-credits.json")) {
    Remove-Item (Join-Path $env:USERPROFILE ".claude\$f") -Force
}

Write-Host ""
Write-Host "卸载完成。正在运行的 Claude Code 会话重启后 hooks 即完全失效。" -ForegroundColor Green
