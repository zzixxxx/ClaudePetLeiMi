#!/bin/bash
# ClaudePetLeiMi macOS 卸载
# 用法: bash uninstall.sh
PLIST="$HOME/Library/LaunchAgents/com.claudepet.leimi.plist"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
if [ -f "$SRC_DIR/claude_pet_mac.py" ]; then
    DEST="$SRC_DIR"
else
    DEST="$HOME/Library/Application Support/ClaudePetLeiMi"
fi
echo "== ClaudePetLeiMi 卸载 (macOS) =="

# 1. 停止桌宠 (按 pid 文件精确停)
if [ -f "$DEST/pet-mac.pid" ]; then
    kill "$(cat "$DEST/pet-mac.pid")" 2>/dev/null && echo "已停止桌宠"
fi

# 2. 移除 hooks / statusLine
[ -f "$DEST/install_hooks.py" ] && python3 "$DEST/install_hooks.py" --remove

# 3. 移除 LaunchAgent
launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST"

# 4. 删除安装目录与状态文件 (开发目录含 .git 时保留文件)
if [ -d "$DEST/.git" ]; then
    echo "检测到开发目录(.git), 跳过文件删除"
else
    rm -rf "$DEST"
fi
for f in cc-pet-state.json cc-pet-sessions.json cc-pet-usage.json \
         cc-pet-credits.json cc-pet-usage-hist.json; do
    rm -f "$HOME/.claude/$f"
done

echo "卸载完成。正在运行的 Claude Code 会话重启后 hooks 即完全失效。"
