#!/usr/bin/env python3
"""抓個股新聞與 Bursa 官方公告。

兩個來源，互補：
  1. Google News RSS  —— 媒體報導（The Edge、The Star、NST、Bernama⋯）
  2. klsescreener     —— Bursa 官方公告（財報、派息、股權變動⋯）

官方公告是「到底發生什麼事」的第一手來源，媒體報導則有解讀與脈絡。
兩邊都抓不到時回傳空 list，不會讓整個更新失敗。
"""

import html as htmlmod
import json
import os
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
    """把日期變成「3 小時前」「昨天」這種好讀的相對時間。"""
    now = datetime.now(MYT)
    hours = (now - dt).total_seconds() / 3600
    if hours < 1:
        return "剛剛"
    if hours < 24:
        return "{} 小時前".format(int(hours))
    days = (now.date() - dt.date()).days
    if days <= 1:
        return "昨天"
    if days < 30:
        return "{} 天前".format(days)
    if days < 365:
        return "{} 個月前".format(days // 30)
    return "{} 年前".format(days // 365)


def _gnews_window(query, window):
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

        ts, iso, when = 0, "", ""
        pub = item.findtext("pubDate")
        if pub:
            try:
                dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z")
                dt = dt.replace(tzinfo=timezone.utc).astimezone(MYT)
                ts, iso, when = dt.timestamp(), dt.strftime("%Y-%m-%d"), _ago(dt)
            except Exception:
                pass
        out.append({"title": htmlmod.unescape(title.strip()), "url": link,
                    "source": source, "when": when, "date": iso, "ts": ts})
    return out


# 不是新聞報導的來源：社群貼文、預測農場、產品部落格、無關的生活新聞。
# 時間排序之後這類內容特別容易擠到前面，必須先濾掉。
NOISE_SOURCES = {
    "moomoo", "pluang", "unisba media", "techpowerup", "nvidia", "nvidia developer",
    "worldfirst.com", "hubbis", "the business manual", "wake up singapore", "says.com",
    "business manual",
    # 自動生成的財經文，稽核時發現內容常與事實不符，卻常排在第一則
    "ad hoc news",
}
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")
_CASHTAG = re.compile(r"\$[A-Z]{2,}")


def _is_noise(a):
    src = (a.get("source") or "").strip().lower()
    if src in NOISE_SOURCES:
        return True
    t = a.get("title") or ""
    # 社群貼文的特徵：表情符號、$股票代號、第一人稱「Me:」
    return bool(_EMOJI.search(t) or _CASHTAG.search(t) or re.search(r"\bMe:", t))


_WORD = re.compile(r"[a-z0-9]{3,}")

# 股價異動的字眼：使用者最怕漏掉的就是「某某股大跌」這種報導
_MOVE = re.compile(r"\b(tumbl|plung|slump|slid|slide|sink|sank|fall|fell|drop|dive|"
                   r"crash|jump|surg|soar|rall|spike|rebound|hits? (a )?\d+-(week|month|year) low)",
                   re.I)
# 一篇講很多家公司的綜合報導，對單一公司來說資訊量很低
_ROUNDUP = re.compile(r"^(insider moves|trading ideas|stocks to watch|market movers|"
                      r"stocks? on the move|what to expect)", re.I)
# 券商研究部對「別家公司」的評等：標題裡有 Maybank，講的卻不是 Maybank
# 「這家銀行的研究部門對別家公司的評級」：Maybank Research Downgrades First Resources、
# RHB keeps Apac Realty at buy。標題裡有公司名，但講的是別人。
# 只在「公司名（+ Research/IB 等）緊接著評級動詞」時才算，
# 「House Keeps Outperform on Public Bank」這種評的是自己的，不能降級
_BROKER_VERB = (r"(?:\s+(?:research|ib|investment bank|securities|capital|bank|group|"
                r"digital|islamic))*\s+(?:maintains|keeps|reiterates|upgrades?|downgrades?|"
                r"initiates|rates|raises|cuts|lifts|trims|retains|sets|starts)\b")


_STOP = {"the", "and", "for", "from", "with", "after", "over", "into", "amid",
         "its", "has", "have", "will", "says", "said", "on", "to", "of", "in"}


def _tokens(t):
    return set(_WORD.findall(t.lower())) - _STOP


_MONEY = re.compile(r"(\d+(?:\.\d+)?)\s*(?:bil(?:lion)?|mil(?:lion)?|b\b|m\b)", re.I)
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|per ?cent|sen\b)", re.I)
_DATE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b", re.I)


def _prints(rx, t):
    return {"".join(g).lower().rstrip("0").rstrip(".") for g in
            (m.groups() for m in rx.finditer(t))}


def _same_event(a, b):
    """兩則標題是不是在講同一件事。

    只比對字詞擋不住「同一事件換個寫法」：
      Maybank finalises RM2.5bil sukuk ／ Maybank Issues 2.5 Billion Ringgit in AT1 Islamic Bonds
      Oct 23 ruling on bid to restrain RHB ... ／ High Court to rule Oct 23 on bid to stop RHB ...
    所以另外用金額、百分比、日期當「指紋」：
      - 字詞重疊 ≥ 四成 → 同一件
      - 同一個金額（億、百萬）而且至少一個相同的字（通常就是公司名）→ 同一件
      - 同一個百分比或日期，而且至少兩個相同的字 → 同一件
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    common = ta & tb
    if len(common) / len(ta | tb) >= 0.4:
        return True
    if _prints(_MONEY, a) & _prints(_MONEY, b) and common:
        return True
    return bool((_prints(_PCT, a) & _prints(_PCT, b)) or
                (_prints(_DATE, a) & _prints(_DATE, b))) and len(common) >= 2


# 公益活動、信用卡／App 推廣、粉絲排隊這類報導跟股價無關，
# 就算標題提到公司名也排到最後，免得擠掉真正會影響股價的消息
_SOFT = re.compile(r"\b(youths?|students?|scholarships?|asnaf|b40|charity|donat\w*|csr|"
                   r"contest|festival|fans|queue|playground|perks|cash ?back|debit card|"
                   r"credit card|app\b|campaign|giveaway|sponsor\w*|makeover|renovation)", re.I)


def _tier(a, names):
    """關聯度分級：0 = 講這家公司而且是股價異動，1 = 講這家公司，2 = 其他（含公益、促銷）。"""
    t = a["title"]
    low = t.lower()
    mentions = any(re.search(r"\b" + re.escape(n.lower()) + r"\b", low) for n in names)
    if not mentions or _ROUNDUP.search(t) or t.count(",") >= 3:
        return 2
    if any(re.match(r"\s*" + re.escape(n.lower()) + _BROKER_VERB, low) for n in names):
        return 2
    # 股價異動的字眼要搭配幅度（%、令吉、仙、N 個月低點）才算，
    # 否則「BTS 限量簽帳卡 drops」這種「推出」也會被當成股價下跌
    moved = _MOVE.search(t) and re.search(
        r"\d+(\.\d+)?\s*%|RM\s?\d|\d+\s*sen\b|\d+-(week|month|year)", t, re.I)
    if moved:
        return 0
    return 2 if _SOFT.search(t) else 1


def google_news(query, limit=6, aliases=()):
    """媒體報導：先看跟這家公司有沒有關，再看新不新。

    演進：
    - 最早是 90 天窗口、照 Google 相關性取前 6 則——最前面常是兩個月前的舊聞。
    - 改成最新優先後，又被企業社會責任新聞、多公司綜合報導、券商評別家公司、
      同一事件的重複報導佔滿，使用者最在意的「RHB 大跌 9%」反而被擠掉。
    - 冷門股的 7 天窗口會被 Google 寬鬆比對到的無關報導湊滿 6 則，
      於是不再往外擴，真正的公司新聞永遠抓不到。

    現在：窗口從 1 天開始（熱門股的 7 天窗口會碰到 Google 100 則上限，
    而那 100 則是依相關性挑的、不是最新的），只有「真的在講這家公司」的報導
    才算進數量，不夠才往外擴；同一事件只留一則。
    """
    names = [query] + [a for a in aliases if a]
    seen_titles, pool = [], []
    for window in ("1d", "7d", "30d", "90d"):
        for a in _gnews_window(query, window):
            if _is_noise(a) or any(_same_event(a["title"], t) for t in seen_titles):
                continue
            seen_titles.append(a["title"])
            a["tier"] = _tier(a, names)
            pool.append(a)
        if sum(1 for a in pool if a["tier"] < 2) >= limit:
            break

    pool.sort(key=lambda a: (a["tier"], -a["ts"]))
    return [{k: v for k, v in a.items() if k != "tier"} for a in pool[:limit]]


_MARKET = re.compile(r"\b(klci|bursa|market|stocks|shares|equities|foreign (funds|investors)|"
                     r"ringgit|index|blue[- ]chips?)\b", re.I)
_CLOSE = re.compile(r"\b(ends?|closes?|closed|finish(es|ed)?|at close|ends? (the )?week|"
                    r"settles?)\b", re.I)


def market_headlines(limit=6):
    """馬股大盤的最新消息——回答「今天市場為什麼這樣走」。

    稽核發現原本前 3 則全是同一件「開盤反彈」，前一天「KLCI 跌到九個月低點」的
    收盤新聞完全沒出現。現在：同事件只留一則、排除個股新聞、
    休市時把最近一則收盤總結排在最前面、交易時段內保證它在前 3 則。
    """
    seen_titles, pool = [], []
    for q in ("Bursa Malaysia", "FBM KLCI", "Malaysian stocks"):
        for a in _gnews_window(q, "3d"):
            t = a["title"]
            if _is_noise(a) or not _MARKET.search(t):
                continue
            if any(_same_event(t, x) for x in seen_titles):
                continue
            seen_titles.append(t)
            pool.append(a)
    pool.sort(key=lambda a: -a["ts"])

    # 收盤總結才會講「為什麼」。休市時（晚上、清晨、週末）把最近一則排第一；
    # 交易時段內最新的盤中消息比昨天的收盤重要，收盤總結只保證留在前 3 則。
    # 期貨的收盤不算——「KLCI Futures End Lower」沒有解釋任何事
    close = next((a for a in pool if _CLOSE.search(a["title"])
                  and "futures" not in a["title"].lower()), None)
    if close:
        now = datetime.now(MYT)
        trading = now.weekday() < 5 and (9, 0) <= (now.hour, now.minute) < (17, 0)
        pos = pool.index(close)
        if not trading:
            pool.insert(0, pool.pop(pos))
        elif pos > 2:
            pool.insert(2, pool.pop(pos))
    return pool[:limit]


def bursa_announcements(code, limit=12):
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
    """持股變動公告是表格式的（四個欄位名，接著四個值）。回傳 dict 或 None。

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
        return None
    if i + 7 >= len(lines):
        return None

    date, qty, txn, nature = lines[i + 4], lines[i + 5], lines[i + 6], lines[i + 7]
    if not re.match(r"^\d", qty.replace(",", "")):
        return None

    holder = after("Name of registered holder") or after("Name")
    total = after("Total no of securities after change")
    pct = after("Direct (%)")

    act = TXN.get(txn.strip().lower(), txn.strip())
    direct = "直接持股" if "direct" in nature.lower() else "間接持股"

    # 同一筆成交會同時觸發第138條（大股東）與第219條（董事）兩份公告，
    # 內容幾乎一樣。附上可比對的識別碼，讓上層能把重複的合成一筆。
    # 交易日兩份的格式不同（2026-09-24 / 24/09/2026），所以不放進鍵裡。
    key = "|".join([re.sub(r"[^a-z0-9]", "", (holder or "").lower())[:28],
                    act, qty.replace(",", ""), (pct or "").strip()])
    is_director = "219" in page[:200000] and "Director" in page[:200000]

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
    return {"text": " ".join(parts), "key": key, "holder": holder,
            "act": act, "shares": qty.replace(",", ""), "pct": (pct or "").strip()}


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

    info = _shareholding_summary(page)
    if info:
        return "", info

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


# ---------------------------------------------------------------- 媒體內文
URL_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "data", "url_cache.json")
_url_cache = None

# 網站自己的固定文字，不是文章內容
CHROME = (
    "stay signed in", "click on the points", "subscribe", "sign up", "log in",
    "cookie", "newsletter", "all rights reserved", "follow us", "download the",
    "copyright", "terms of use", "privacy policy", "advertisement",
    "preferred source", "home highlight", "read also", "read more",
    "sign in to", "register now", "share this", "已訂閱", "請登入",
)

# 馬國媒體的內文幾乎都以地名開頭（KUALA LUMPUR (Aug 27): ...）。
# 找到它就把前面的網站樣板全部切掉——那段前綴不只難看，
# 還會讓翻譯引擎失去上下文，整段跟著崩掉。
DATELINE = re.compile(
    r"\b(KUALA LUMPUR|PETALING JAYA|PUTRAJAYA|GEORGE TOWN|JOHOR BAHRU|KUCHING|"
    r"KOTA KINABALU|SINGAPORE|HONG KONG|NEW YORK|LONDON|TOKYO|SHANGHAI|JAKARTA)"
    r"\b\s*(\([^)]{2,30}\))?\s*:")


def _load_url_cache():
    global _url_cache
    if _url_cache is None:
        try:
            with open(URL_CACHE_PATH, encoding="utf-8") as fh:
                _url_cache = json.load(fh)
        except Exception:
            _url_cache = {}
    return _url_cache


def save_url_cache():
    if _url_cache is None:
        return
    os.makedirs(os.path.dirname(URL_CACHE_PATH), exist_ok=True)
    with open(URL_CACHE_PATH, "w", encoding="utf-8") as fh:
        json.dump(_url_cache, fh, ensure_ascii=False, indent=0, sort_keys=True)


def resolve_google_url(url):
    """把 Google News 的轉址網址換成發布商的真實網址。

    Google 把真實網址藏在 JS 後面，要先從文章頁抓簽章與時間戳，
    再打它內部的 batchexecute RPC 才拿得到。回應是
    )]}' 開頭、內層又是 JSON 字串的格式，網址在 garturlres 後面。
    """
    if "news.google.com" not in url:
        return url

    cache = _load_url_cache()
    if url in cache:
        return cache[url]

    try:
        aid = url.split("/articles/")[1].split("?")[0]
        page = _fetch(url)
        sig = re.search(r'data-n-a-sg="([^"]+)"', page)
        ts = re.search(r'data-n-a-ts="([^"]+)"', page)
        if not (sig and ts):
            return url

        inner = json.dumps(["garturlreq",
                            [["X", "X", ["X", "X"], None, None, 1, 1, "MY:en", None,
                              1, None, None, None, None, None, 0, 1],
                             "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
                            aid, int(ts.group(1)), sig.group(1)])
        payload = json.dumps([[["Fbv4je", inner, None, "generic"]]])
        req = urllib.request.Request(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            data=urllib.parse.urlencode({"f.req": payload}).encode(),
            headers={"User-Agent": UA,
                     "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
        with urllib.request.urlopen(req, timeout=25,
                                    context=ssl.create_default_context()) as r:
            out = r.read().decode("utf-8", "replace")

        m = re.search(r'garturlres\\?",\\?"(https?://[^"\\]+)', out)
        real = m.group(1) if m else url
    except Exception:
        real = url

    cache[url] = real
    return real


def article_body(url, limit=900):
    """抓媒體報導的內文。回傳純文字，抓不到就回空字串。"""
    if "news.google.com" in url:
        return ""
    try:
        page = _fetch(url)
    except Exception:
        return ""

    # 有結構化資料就用它，最乾淨
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                            page, re.S):
        if "articleBody" not in block:
            continue
        m = re.search(r'"articleBody"\s*:\s*"((?:[^"\\]|\\.)*)"', block)
        if m:
            try:
                t = m.group(1).encode().decode("unicode_escape")
            except Exception:
                t = m.group(1)
            t = re.sub(r"\s+", " ", htmlmod.unescape(t)).strip()
            if len(t) > 150:
                return _trim(_strip_chrome(t), limit)

    # 否則退回抓段落。注意順序：先合併再切樣板，不能逐段丟。
    # 有些網站會把樣板和導言塞在同一個 <p>，整段丟掉會連第一句正文一起丟。
    paras = []
    for raw in re.findall(r"<p[^>]*>(.*?)</p>", page, re.S):
        t = re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()
        if len(t) < 40:
            continue
        # 只丟「整段都是樣板」的短段落
        if len(t) < 160 and any(k in t.lower() for k in CHROME):
            continue
        paras.append(t)
    return _trim(_strip_chrome(" ".join(paras)), limit) if paras else ""


def _strip_chrome(t):
    """切掉開頭的網站樣板，從真正的內文開始。

    馬國媒體的導言幾乎都以地名開頭，找到地名就從那裡起算；
    找不到就退而求其次，切掉開頭已知的樣板片語。
    """
    m = DATELINE.search(t[:600])
    if m:
        return t[m.start():].strip()
    low = t.lower()
    for k in CHROME:
        i = low.find(k)
        if 0 <= i < 200:
            nxt = t.find(". ", i)
            if 0 < nxt < 400:
                return t[nxt + 2:].strip()
    return t


def _trim(t, limit):
    if len(t) <= limit:
        return t
    cut = t[:limit]
    dot = cut.rfind(". ")
    return (cut[:dot + 1] if dot > limit * 0.5 else cut).strip() + " …"


def fetch(stock):
    """回傳 {'articles': [...], 'filings': [...]}"""
    query = stock.get("q") or re.sub(r"[^\w\s&().-]", " ", stock.get("name", "")).strip()
    # Bursa 代碼可能帶字尾（例如 KLCCP 的 5235SS），指數則沒有代碼
    code = stock["symbol"].split(".")[0]
    has_code = bool(re.match(r"^\d{4}[A-Z]{0,3}$", code))
    return {
        "articles": google_news(query, aliases=stock.get("aliases", ())) if query else [],
        "filings": bursa_announcements(code) if has_code else [],
    }


if __name__ == "__main__":
    import json, sys
    name = sys.argv[1] if len(sys.argv) > 1 else "Sentral REIT"
    code = sys.argv[2] if len(sys.argv) > 2 else "5123"
    print(json.dumps(fetch({"q": name, "name": name, "symbol": code + ".KL"}),
                     ensure_ascii=False, indent=2))
