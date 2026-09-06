#!/bin/bash
# 把每日更新裝成 macOS LaunchAgent，每天收盤後自動跑一次。
# 用法：./install_daily.sh          （預設每天 18:30，馬股 5pm 收盤後）
#      ./install_daily.sh 20 00    （改成每天 20:00）
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
HOUR="${1:-18}"; MIN="${2:-30}"
LABEL="com.stockchecker.daily"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$(command -v python3)"

mkdir -p "$HOME/Library/LaunchAgents" "$DIR/logs"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$DIR/fetch.py</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$DIR/logs/daily.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/daily.err</string>
</dict>
</plist>
PLISTEOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
printf '已設定：每天 %02d:%02d 自動更新\n' "$HOUR" "$MIN"
echo "紀錄檔：$DIR/logs/daily.log"
echo "要取消：launchctl unload $PLIST && rm $PLIST"
echo "想馬上跑一次測試：launchctl start $LABEL"
