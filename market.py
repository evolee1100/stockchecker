#!/usr/bin/env python3
"""全市場報價（漲跌排行）與 IPO。

兩者都來自 klsescreener，靜態 HTML 即可取得，不需要執行 JS：

  /v2/screener/quote_results   全 Bursa 約 1,150 檔的報價、漲跌、市值、類別
  /v2/ipos                     即將上市與已上市的 IPO，含上市日、IPO 價、板別

追蹤清單只有 49 檔，看不到自己沒追的股票發生什麼事。
這個模組補上「整個市場今天怎麼了」與「最近有什麼新股」。
"""

import html as htmlmod
import re
import ssl
import urllib.request
from datetime import date, datetime, timedelta, timezone

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
MYT = timezone(timedelta(hours=8))
BASE = "https://www.klsescreener.com"

MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def _fetch(path, timeout=40):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout,
                                context=ssl.create_default_context()) as r:
        return r.read().decode("utf-8", "replace")


# Bursa 的行業欄是「子行業 + 主行業」直接接在一起（例如 "Banking Financial Services"），
# 用主行業的字尾對照成中文短名，排行榜上才看得懂
SECTOR_ZH = [
    ("Real Estate Investment Trusts", "REIT"),
    ("Financial Services", "金融"),
    ("Industrial Products & Services", "工業"),
    ("Consumer Products & Services", "消費"),
    ("Telecommunications & Media", "電訊媒體"),
    ("Transportation & Logistics", "運輸物流"),
    ("Technology", "科技"),
    ("Energy", "能源"),
    ("Property", "地產"),
    ("Plantation", "種植"),
    ("Construction", "建築"),
    ("Health Care", "醫療"),
    ("Utilities", "公用事業"),
]


def sector_zh(sector):
    s = (sector or "").strip()
    for en, zh in SECTOR_ZH:
        if s.endswith(en) or s.lower() == en.lower():
            return zh
    return s.split(" ")[0] if s else ""


def _text(fragment):
    t = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", htmlmod.unescape(t)).strip()


def _num(s):
    try:
        return float(str(s).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 全市場報價

def quotes():
    """回傳全市場報價清單。欄位順序是對照真實頁面逐欄確認過的。"""
    page = _fetch("/v2/screener/quote_results?getquote=1&board=1")
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        tds = re.findall(r"<td([^>]*)>(.*?)</td>", row, re.S)
        if len(tds) < 16:
            continue
        cell = [_text(v) for _, v in tds]
        full = re.search(r'title="([^"]*)"', tds[0][0])
        cat = cell[2]
        out.append({
            "short": re.sub(r"\s*\[s\]\s*$", "", cell[0]),
            "name": htmlmod.unescape(full.group(1)) if full else cell[0],
            "code": cell[1],
            "sector": cat.rsplit(",", 1)[0].strip() if "," in cat else cat,
            "sector_zh": sector_zh(cat.rsplit(",", 1)[0].strip() if "," in cat else cat),
            # 統一成小寫比對。網站全市場報價寫 "Ace Market"、IPO 頁寫 "ACE Market"，
            # 原本用 "ACE Market" 比對，創業板 269 檔全部被靜默排除
            "board": cat.rsplit(",", 1)[1].strip() if "," in cat else "",
            "board_key": (cat.rsplit(",", 1)[1].strip() if "," in cat else "").lower(),
            "price": _num(cell[3]),
            "change": _num(cell[4]),
            "pct": _num(cell[5]),
            "range52": cell[6],
            "volume": _num(cell[7]),          # 單位是「手」（100 股）
            "pe": _num(cell[11]),
            "dy": _num(cell[12]),
            "mcap": _num(cell[15]),           # 百萬馬幣
        })
    return out


def movers(qs, n=5):
    """全市場漲跌前 n 名。

    不過濾的話，排行榜會被仙股佔滿：RM0.005 漲到 RM0.010 就是 +100%，
    那只是跳一個最小升降單位，沒有資訊量。所以只看：
      - 主板與創業板的普通股（代碼純四位數；排除權證、ETF、LEAP 市場）
      - 股價 ≥ RM0.20（低於這個價位，一個最小單位就是 2.5% 以上）
      - 市值 ≥ RM2 億
      - 當天成交金額 ≥ RM30 萬（用金額不用手數，否則高價大型股會被誤排除）
    """
    ok = []
    for q in qs:
        if not re.fullmatch(r"\d{4}", q["code"] or ""):
            continue
        if q["board_key"] not in ("main market", "ace market"):
            continue
        if q["pct"] is None or q["price"] is None:
            continue
        # 流動性用「成交金額」而不是「成交手數」判斷。手數門檻會把高價大型股
        # （豐隆銀行一手就要兩千多令吉）排除，卻讓成交才幾萬令吉的低價股過關。
        turnover = (q["volume"] or 0) * 100 * q["price"]
        if q["price"] < 0.20 or (q["mcap"] or 0) < 200 or turnover < 300_000:
            continue
        ok.append(q)

    up = sorted((q for q in ok if q["pct"] > 0), key=lambda q: -q["pct"])[:n]
    down = sorted((q for q in ok if q["pct"] < 0), key=lambda q: q["pct"])[:n]
    return {"up": up, "down": down, "universe": len(ok), "total": len(qs)}


def sector_breadth(qs):
    """全市場各行業的漲跌家數。用來判斷「整個產業在跌」，不只看自己追的幾檔。"""
    by = {}
    for q in qs:
        if not re.fullmatch(r"\d{4}", q["code"] or "") or q["pct"] is None:
            continue
        if q["board_key"] not in ("main market", "ace market"):
            continue
        s = by.setdefault(q["sector"], {"up": 0, "down": 0, "flat": 0, "n": 0})
        s["n"] += 1
        s["up" if q["pct"] > 0 else "down" if q["pct"] < 0 else "flat"] += 1
    return by


# ---------------------------------------------------------------- IPO

def ipos(today=None):
    """即將上市與最近上市的 IPO。

    每一筆是頁面上的一張卡片：左邊是上市日（月、日、年在 title 屬性），
    右邊是代碼、名稱、認購期、發行量、市值、板別、行業，最右邊是 IPO 價。
    """
    today = today or datetime.now(MYT).date()
    page = _fetch("/v2/ipos")
    past_at = page.find("Past IPOs")

    items = []
    for m in re.finditer(r'<div class="card mb-3 p-0">', page):
        start = m.start()
        nxt = page.find('<div class="card mb-3 p-0">', start + 10)
        block = page[start:nxt if nxt > 0 else start + 6000]

        year = re.search(r'title="(\d{4})"', block)
        mon = re.search(r'text-uppercase">\s*([A-Za-z]{3})\s*<', block)
        day = re.search(r"<h3>\s*(\d{1,2})\s*</h3>", block)
        link = re.search(r'href="/v2/stocks/view/([0-9A-Z]+)">([^<]+)</a>', block)
        if not (mon and day and link):
            continue
        y = int(year.group(1)) if year else today.year
        try:
            listed = date(y, MONTHS[mon.group(1).title()], int(day.group(1)))
        except (KeyError, ValueError):
            continue

        name = re.search(r'</a></h4><span class="ml-3">([^<]+)</span>', block)

        def field(label):
            f = re.search(label + r":\s*</span>\s*(?:<strong>)?\s*([^<]+?)\s*<", block)
            return htmlmod.unescape(f.group(1)).strip() if f else ""

        # IPO 價直接錨定 title="Issue price" 的元素。原本取「卡片裡最後一個小數」，
        # 遇到整數價（RM1 在頁面上寫成 "1"）就抓不到
        pm = re.search(r'title="Issue price"[^>]*>\s*([\d.]+)\s*<', block)
        price = _num(pm.group(1)) if pm else None
        items.append({
            "code": link.group(1),
            "short": htmlmod.unescape(link.group(2)).strip(),
            "name": htmlmod.unescape(name.group(1)).strip() if name else "",
            "listing": listed.isoformat(),
            "upcoming": start < past_at if past_at > 0 else listed >= today,
            "open": field("Open"),
            "close": field("Close"),
            "issue_size": _num(field("Issue Size")),
            "mcap": _num(field("Market Cap")),
            "board": field("Board"),
            "sector": field("Sector").title(),
            "sector_zh": sector_zh(field("Sector").title()),
            "ipo_price": price,
        })
    return items


def ipo_summary(qs, today=None, recent_days=90):
    """整理成網頁要的樣子：即將上市、最近上市（附上市後表現）。"""
    today = today or datetime.now(MYT).date()
    by_code = {q["code"]: q for q in qs}
    up, recent = [], []
    for it in ipos(today):
        d = date.fromisoformat(it["listing"])
        # 上市當天已經開始交易，要歸到「最近上市」才看得到首日表現
        if d > today:
            it["days_to"] = (d - today).days
            up.append(it)
        elif (today - d).days <= recent_days:
            q = by_code.get(it["code"])
            if q and q["price"] and it["ipo_price"]:
                it["price"] = q["price"]
                it["since_ipo"] = round((q["price"] / it["ipo_price"] - 1) * 100, 2)
                it["pct_today"] = q["pct"]
            it["days_since"] = (today - d).days
            recent.append(it)
    up.sort(key=lambda x: x["listing"])
    recent.sort(key=lambda x: x["listing"], reverse=True)
    return {"upcoming": up, "recent": recent}


if __name__ == "__main__":
    qs = quotes()
    print("全市場 {} 檔".format(len(qs)))
    mv = movers(qs)
    print("\n篩選後可比較 {} 檔".format(mv["universe"]))
    print("\n漲最多：")
    for q in mv["up"]:
        print("  {:+6.1f}%  {:<10} {:>7.3f}  市值 RM{:>7,.0f}M  {}".format(
            q["pct"], q["short"], q["price"], q["mcap"], q["sector_zh"]))
    print("\n跌最多：")
    for q in mv["down"]:
        print("  {:+6.1f}%  {:<10} {:>7.3f}  市值 RM{:>7,.0f}M  {}".format(
            q["pct"], q["short"], q["price"], q["mcap"], q["sector_zh"]))
    s = ipo_summary(qs)
    print("\n即將上市 {} 檔：".format(len(s["upcoming"])))
    for i in s["upcoming"]:
        print("  {} ({:>2} 天後) {:<9} IPO RM{}  {}  {}".format(
            i["listing"], i["days_to"], i["short"], i["ipo_price"], i["board"], i["sector"][:24]))
    print("\n最近上市 {} 檔：".format(len(s["recent"])))
    for i in s["recent"]:
        print("  {} {:<9} IPO {:<7} 現價 {:<6} 上市後 {}  {}".format(
            i["listing"], i["short"],
            "RM{}".format(i["ipo_price"]) if i["ipo_price"] else "—",
            i.get("price", "—"),
            "{:+.1f}%".format(i["since_ipo"]) if "since_ipo" in i else "—",
            i["board"]))
