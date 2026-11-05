#!/bin/bash
# 打包 GoldPrice.app 并部署到 ~/Applications/
#
# 关键点：打包后往 Info.plist 写入 LSUIElement=true —— 纯菜单栏插件模式，
# 启动后不在 Dock 显示图标（像其他菜单栏工具一样），也不占用 Cmd+Tab。
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME=GoldPrice
DEST="$HOME/Applications/${APP_NAME}.app"

echo "==> 生成规范应用图标"
python3 generate_icon.py

echo "==> PyInstaller 打包"
.venv/bin/pyinstaller --noconfirm --clean --windowed --name "$APP_NAME" \
  --icon build/icon.icns --collect-data plotly \
  --osx-bundle-identifier com.bruce.goldprice main.py

echo "==> 写入 LSUIElement=true（隐藏 Dock 图标）"
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "dist/${APP_NAME}.app/Contents/Info.plist"

echo "==> 部署到 $DEST"
rm -rf "$DEST"
ditto "dist/${APP_NAME}.app" "$DEST"
touch "$DEST"

echo "==> 完成"
