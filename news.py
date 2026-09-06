#!/usr/bin/env python3
"""抓個股新聞與 Bursa 官方公告。

兩個來源，互補：
  1. Google News RSS  —— 媒體報導（The Edge、The Star、NST、Bernama⋯）
  2. klsescreener     —— Bursa 官方公告（財報、派息、股權變動⋯）

官方公告是「到底發生什麼事」的第一手來源，媒體報導則有解讀與脈絡。
兩邊都抓不到時回傳空 list，不會讓整個更新失敗。
"""

import html as htmlmod
import re
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
MYT = timezone(timedelta(hours=8))

GNEWS = "https://news.google.com/rss/search?q={q}&hl=en-MY&gl=MY&ceid=MY:en"
KLSE = "https://www.klsescreener.com/v2/announcements/stock/{code}"

MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def _fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout,
                                context=ssl.create_default_context()) as r:
        return r.read().decode("utf-8", "replace")


def _ago(dt):
    """把日期變成「3 天前」這種好讀的相對時間。"""
    days = (datetime.now(MYT).date() - dt.date()).days
    if days <= 0:
        return "今天"
    if days == 1:
        return "昨天"
    if days < 30:
        return "{} 天前".format(days)
    if days < 365:
        return "{} 個月前".format(days // 30)
    return "{} 年前".format(days // 365)


def google_news(query, limit=6, window="90d"):
    """媒體報導。query 例如 'Sentral REIT'。"""
    q = urllib.parse.quote('"{}" when:{}'.format(query, window))
    try:
        root = ET.fromstring(_fetch(GNEWS.format(q=q)))
    except Exception:
        return []

    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        # Google 一律把 " - 媒體名稱" 接在標題後面，拆掉才不會跟下面那行重複
        source = ""
        src_el = item.find("source")
        if src_el is not None and src_el.text:
            source = src_el.text.strip()
        if " - " in title:
            head, tail = title.rsplit(" - ", 1)
            if head and len(tail) <= 45:
                title = head
                source = source or tail

        when, iso = "", ""
        pub = item.findtext("pubDate")
        if pub:
            try:
                dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z")
                dt = dt.replace(tzinfo=timezone.utc).astimezone(MYT)
                when, iso = _ago(dt), dt.strftime("%Y-%m-%d")
            except Exception:
                pass

        out.append({"title": htmlmod.unescape(title.strip()), "url": link,
                    "source": source, "when": when, "date": iso})
        if len(out) >= limit:
            break
    return out


def bursa_announcements(code, limit=6):
    """Bursa 官方公告。code 是純數字代碼，例如 '5123'。"""
    try:
        page = _fetch(KLSE.format(code=code))
    except Exception:
        return []

    blocks = re.findall(
        r'<a href="(/v2/announcements/view/\d+)"[^>]*class="announcement-item">(.*?)</a>',
        page, re.S)

    today = datetime.now(MYT).date()
    out = []
    for href, block in blocks:
        def grab(cls):
            m = re.search(r'class="[^"]*\b{}\b[^"]*"[^>]*>(.*?)<'.format(cls), block, re.S)
            return re.sub(r"\s+", " ", htmlmod.unescape(m.group(1))).strip() if m else ""

        def block_div(name):
            m = re.search(r'<div class="{}">(.*?)</div>'.format(name), block, re.S)
            if not m:
                return ""
            return re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))).strip()

        title = block_div("title")
        snippet = block_div("snippet")
        # 一般公告的 title 常常只寫 "OTHERS"，真正的標題在 snippet 裡
        if title.upper() in ("", "OTHERS") and snippet:
            title = snippet
        elif snippet and snippet.lower() != title.lower():
            title = "{} — {}".format(title, snippet)
        if not title:
            continue
        if len(title) > 150:
            title = title[:147] + "…"

        day, mon = grab("day"), grab("month")
        when, iso = "", ""
        if day.isdigit() and mon in MONTHS:
            # 頁面上沒有年份：月份比現在大就是去年的公告
            year = today.year if MONTHS[mon] <= today.month else today.year - 1
            try:
                dt = datetime(year, MONTHS[mon], int(day), tzinfo=MYT)
                when, iso = _ago(dt), dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

        cat = ""
        m = re.search(r'class="category-tag[^"]*"[^>]*>(.*?)<', block, re.S)
        if m:
            cat = re.sub(r"\s+", " ", htmlmod.unescape(m.group(1))).strip()

        out.append({"title": title, "url": "https://www.klsescreener.com" + href,
                    "source": cat or "Bursa 公告", "when": when, "date": iso})
        if len(out) >= limit:
            break
    return out


def fetch(stock):
    """回傳 {'articles': [...], 'filings': [...]}"""
    query = stock.get("q") or re.sub(r"[^\w\s&().-]", " ", stock.get("name", "")).strip()
    # Bursa 代碼可能帶字尾（例如 KLCCP 的 5235SS），指數則沒有代碼
    code = stock["symbol"].split(".")[0]
    has_code = bool(re.match(r"^\d{4}[A-Z]{0,3}$", code))
    return {
        "articles": google_news(query) if query else [],
        "filings": bursa_announcements(code) if has_code else [],
    }


if __name__ == "__main__":
    import json, sys
    name = sys.argv[1] if len(sys.argv) > 1 else "Sentral REIT"
    code = sys.argv[2] if len(sys.argv) > 2 else "5123"
    print(json.dumps(fetch({"q": name, "name": name, "symbol": code + ".KL"}),
                     ensure_ascii=False, indent=2))
