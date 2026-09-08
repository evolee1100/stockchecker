#!/usr/bin/env python3
"""把 index.html 和資料合成一個獨立檔案 snapshot.html。

單一檔案、不依賴 data/ 目錄，可以直接雙擊開、丟到手機、或傳給別人看。
用法：python3 snapshot.py
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
html = open(os.path.join(HERE, "index.html"), encoding="utf-8").read()
data = open(os.path.join(HERE, "data", "data.js"), encoding="utf-8").read()

import re
import sys

# index.html 現在是用 fetch 抓資料（避開瀏覽器快取），沒有 script 標籤可以取代。
# 單一檔案版本要把資料直接內嵌，掛在主程式之前讓它跳過 fetch。
out = html.replace("<script>\nlet D = window.STOCK_DATA",
                   "<script>\n" + data + "</script>\n<script>\nlet D = window.STOCK_DATA")
path = os.path.join(HERE, "snapshot.html")
open(path, "w", encoding="utf-8").write(out)
print("已產生 {} （{:.0f} KB，單一檔案可直接開）".format(path, len(out) / 1024))

# --artifact：Artifact 平台會自己包 <html>/<head>/<body>，所以要把外層拆掉
if "--artifact" in sys.argv:
    body = re.search(r"<body>(.*)</body>", out, re.S).group(1)
    title = re.search(r"<title>(.*?)</title>", out, re.S).group(1)
    style = re.search(r"<style>.*?</style>", out, re.S).group(0)
    art = "<title>{}</title>\n{}\n{}".format(title, style, body)
    apath = os.path.join(HERE, "artifact.html")
    open(apath, "w", encoding="utf-8").write(art)
    print("已產生 {} （{:.0f} KB，給 Artifact 用）".format(apath, len(art) / 1024))
