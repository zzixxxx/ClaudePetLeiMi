#!/bin/bash
# ClaudePetLeiMi macOS 一键安装
# 用法:
#   curl -fsSL https://raw.githubusercontent.com/zzixxxx/ClaudePetLeiMi/main/install.sh | bash
# 或在仓库目录内直接: bash install.sh
set -e

REPO_ZIP="https://github.com/zzixxxx/ClaudePetLeiMi/archive/refs/heads/main.zip"
PLIST="$HOME/Library/LaunchAgents/com.claudepet.leimi.plist"

echo "== ClaudePetLeiMi 安装 (macOS) =="

# 0. 前置: python3
if ! command -v python3 >/dev/null 2>&1; then
    echo "缺少 python3, 请先安装 (xcode-select --install 或 brew install python)" >&2
    exit 1
fi

# 1. 定位/下载安装目录: 脚本旁有 claude_pet_mac.py 就地安装, 否则下载到 ~/Library
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
if [ -f "$SRC_DIR/claude_pet_mac.py" ]; then
    DEST="$SRC_DIR"
else
    DEST="$HOME/Library/Application Support/ClaudePetLeiMi"
    echo "下载到: $DEST"
    TMP=$(mktemp -d)
    curl -fsSL "$REPO_ZIP" -o "$TMP/pet.zip"
    unzip -q "$TMP/pet.zip" -d "$TMP"
    mkdir -p "$DEST"
    cp -R "$TMP/ClaudePetLeiMi-main/." "$DEST/"
    rm -rf "$TMP"
fi
echo "安装目录: $DEST"

# 2. 依赖
echo "安装依赖 (pyobjc / pillow)..."
python3 -m pip install --quiet --user pyobjc-framework-Cocoa pillow

# 3. hooks + statusLine 合并进 ~/.claude/settings.json
python3 "$DEST/install_hooks.py"

# 4. LaunchAgent 开机自启
mkdir -p "$(dirname "$PLIST")"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.claudepet.leimi</string>
    <key>ProgramArguments</key>
    <array>
        <string>$(command -v python3)</string>
        <string>$DEST/claude_pet_mac.py</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>WorkingDirectory</key><string>$DEST</string>
</dict>
</plist>
EOF
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

# 5. 自检 + 立即启动
python3 "$DEST/claude_pet_mac.py" --diag || true
echo ""
echo "安装完成。蕾米埃尔已随 LaunchAgent 启动; 如未出现, 手动执行:"
echo "  python3 \"$DEST/claude_pet_mac.py\""
