#!/bin/bash
# 在本機開一個小網頁伺服器，然後用瀏覽器看儀表板
cd "$(dirname "$0")"
echo "打開瀏覽器 → http://localhost:8777"
python3 -m http.server 8777
