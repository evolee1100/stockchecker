#!/bin/bash
# 本機預覽用的小伺服器。
#
# 加上 no-store 是必要的：data/data.js 用固定網址載入，瀏覽器會快取，
# 開發時改了資料卻看到舊畫面，很容易誤判成程式壞掉（這在開發過程中發生過三次）。
# 線上版是靠部署時在網址加版本號解決，本機這裡直接關掉快取比較單純。
cd "$(dirname "$0")"
echo "打開瀏覽器 → http://localhost:8777"
python3 - "$@" <<'PY'
import http.server, socketserver

class NoCache(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):        # 安靜一點
        pass

socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("", 8777), NoCache) as httpd:
    httpd.serve_forever()
PY
