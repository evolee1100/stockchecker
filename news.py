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


TXN = {"acquired": "增持", "disposed": "減持", "transferred": "轉讓",
       "others": "其他", "sale of shares": "賣出股份", "purchase of shares": "買入股份"}


def _plain_lines(page):
    t = re.sub(r"<script.*?</script>", " ", page, flags=re.S)
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.S)
    t = re.sub(r"<br\s*/?>|</p>|</div>|</td>|</tr>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = htmlmod.unescape(re.sub(r"[ \t]+", " ", t))
    return [l.strip() for l in t.split("\n") if l.strip()]


def _shareholding_summary(page):
    """持股變動公告是表格式的（四個欄位名，接著四個值）。

    這種公告硬丟去機器翻譯只會得到一堆零碎的欄位名，不如直接解析成一句話。
    準確、不用 API、也不會被限流。
    """
    lines = _plain_lines(page)

    def after(label, offset=1):
        for i, l in enumerate(lines):
            if l.lower() == label.lower() and i + offset < len(lines):
                return lines[i + offset].strip()
        return ""

    # 四個標題連在一起，值也連在一起，所以值的位移是 +4
    try:
        i = [k for k, l in enumerate(lines) if l == "Date of change"][0]
    except IndexError:
        return ""
    if i + 7 >= len(lines):
        return ""

    date, qty, txn, nature = lines[i + 4], lines[i + 5], lines[i + 6], lines[i + 7]
    if not re.match(r"^\d", qty.replace(",", "")):
        return ""

    holder = after("Name of registered holder") or after("Name")
    total = after("Total no of securities after change")
    pct = after("Direct (%)")

    act = TXN.get(txn.strip().lower(), txn.strip())
    direct = "直接持股" if "direct" in nature.lower() else "間接持股"

    parts = ["{} 於 {} {} {} 股（{}）。".format(
        holder.title() if holder.isupper() else holder,
        _date_zh(date), act, qty, direct)]
    if total:
        tail = "變動後持有 {} 股".format(total)
        if pct:
            tail += "，佔 {}%".format(pct)
        parts.append(tail + "。")
    reason = after("Circumstances by reason of which change has occurred")
    if reason and len(reason) < 80:
        parts.append("原因：{}。".format(TXN.get(reason.lower(), reason)))
    return " ".join(parts)


def _date_zh(text):
    m = re.match(r"^(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})$", (text or "").strip())
    if m and m.group(2) in MONTHS:
        return "{}-{:02d}-{:02d}".format(m.group(3), MONTHS[m.group(2)], int(m.group(1)))
    return text


def announcement_body(url, limit=900):
    """抓 Bursa 公告內文。

    回傳 (原文, 已是中文的摘要)。持股變動類公告走規則解析，直接產出中文摘要，
    不必經過翻譯 API；其他公告抓英文全文，交給呼叫端去翻。

    媒體報導沒有對應做法——Google News 把真實網址藏在 JS 後面，
    馬國幾家主要媒體也沒有可用的 RSS，所以報導只有標題。
    """
    try:
        page = _fetch(url)
    except Exception:
        return "", ""

    zh = _shareholding_summary(page)
    if zh:
        return "", zh

    m = re.search(r'<div[^>]*class="[^"]*\bcontent\b[^"]*"[^>]*>(.*?)</div>\s*</div>', page, re.S)
    if not m:
        return "", ""

    t = re.sub(r"<script.*?</script>", " ", m.group(1), flags=re.S)
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.S)
    t = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = htmlmod.unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t).strip()

    # 頁面右側的 metadata 會混進內容區，遇到這些標記就截斷
    for marker in ("Announcement Info", "Reference Number", "Attachments",
                   "View original announcement", "Announcement Details"):
        i = t.find(marker)
        if i > 60:
            t = t[:i].strip()

    if len(t) < 40:
        return "", ""

    if len(t) > limit:
        cut = t[:limit]
        dot = max(cut.rfind(". "), cut.rfind("\n"))
        t = (cut[:dot + 1] if dot > limit * 0.5 else cut).strip() + " …"
    return t, ""


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
