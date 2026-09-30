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
            "sub_sector": field("Sub sector").title(),
            "sector_zh": sector_zh(field("Sector").title()),
            "ipo_price": price,
        })
    return items


BOARD_ZH = {"main market": "主板", "ace market": "創業板（ACE）",
            "leap market": "LEAP 板（限資深投資者）"}


def board_zh(board):
    return BOARD_ZH.get((board or "").strip().lower(), board or "")


def ipo_detail(code, short, name=""):
    """IPO 個股頁的「Recent News」：業務簡介、超額認購倍數、中文名、板別。

    IPO 清單卡片只有代號和英文名，看不出公司做什麼、甚至看不出是不是馬股。
    個股頁底下有星洲日報的新股介紹（簡體中文，會寫「主要涉及……業務」）
    和 The Star 的認購結果，從這裡抓。
    """
    try:
        page = _fetch("/v2/stocks/view/" + code, timeout=25)
    except Exception:
        return {}
    items = []
    for li in re.findall(r'<li class="list-group-item[^"]*">(.*?)</li>', page, re.S):
        a = re.search(r'<h6><a[^>]*href="([^"]+)"[^>]*>(.*?)</a></h6>', li, re.S)
        body = re.search(r'<div class="text-justify">(.*?)</div>', li, re.S)
        tm = re.search(r'<time datetime="([^"]+)"', li)
        src = re.search(r'<span class="pull-right[^"]*">\s*([^<]+?)\s*<', li)
        if not a:
            continue
        items.append({"title": _text(a.group(2)), "url": BASE + a.group(1),
                      "summary": re.sub(r"\[vip_content_start\].*", "", _text(body.group(1)) if body else ""),
                      "time": tm.group(1) if tm else "", "source": src.group(1) if src else ""})

    out = {}
    blob = " ".join(i["title"] + " " + i["summary"] for i in items)
    m = (re.search(r"oversubscribed by ([\d.]+) times", blob, re.I)
         or re.search(r"超[额額]认?[购購]率?(?:报|報)?\s*([\d.]+)\s*倍", blob)
         or re.search(r"([\d.]+)\s*倍超[额額]认?[购購]", blob))
    if m:
        out["oversub"] = float(m.group(1))
    m = re.search(r"raise[sd]? (?:about |some |up to )?RM\s?([\d.]+)\s?(mil|million|bil|billion)", blob, re.I)
    if m:
        out["raise_m"] = float(m.group(1)) * (1000 if m.group(2).lower().startswith("bil") else 1)
    # 中文名：星洲寫「GB联合（GBBOND,0475,创业板…）」「Pioneer Heat控股(PIONEER,0471,…)」
    m = re.search(r"((?:[A-Za-z0-9]+ )?[A-Za-z0-9]*[一-鿿]{1,8})\s*[（(]\s*" + re.escape(short)
                  + r"\s*[,，]\s*" + re.escape(code), blob)
    if m:
        out["name_zh"] = re.sub(r"^.*(?:挂牌的|掛牌的|上市的|登场的|登場的|的)", "", m.group(1)).strip()

    # 公司的稱呼：代號、英文名第一個字（至少 3 個字母）、英文名前兩個字、中文名
    words = re.findall(r"[A-Za-z0-9&]+", name or "")
    names = {short.lower()}
    if words and len(words[0]) >= 3:
        names.add(words[0].lower())
    if len(words) >= 2:
        names.add((words[0] + " " + words[1]).lower())
    if out.get("name_zh"):
        names.add(out["name_zh"].lower())
    about_it = lambda t: any(n in t.lower() for n in names)
    # 只看標題就在講這家公司的報導；「IPO Watch: 三家公司」這種合集會張冠李戴
    mine = [i for i in items if about_it(i["title"])] or [i for i in items if about_it(i["summary"][:120])]

    # 業務簡介：先試固定寫法（描述要緊貼在公司名前面），都沒有就挑一則介紹型報導的第一句
    nm = "(?:" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + ")"
    pats = [
        (r"主要(?:涉及|从事|從事|经营|經營|业务为|業務為)([^。；]{4,90}?)(?:等)?(?:业务|業務)", False),
        (r"([A-Za-z0-9一-鿿、，和及与與]{2,30}(?:供应商|供應商|承包商|制造商|製造商|服务商|服務商|开发商|開發商|"
         r"生产商|生產商|提供商|营运商|營運商|经销商|經銷商))\s*[—–-]*\s*(?=" + nm + ")", True),
        (r"((?:[A-Za-z&/-]+ ){1,5}(?:provider|manufacturer|contractor|developer|supplier|specialist|operator|"
         r"producer|distributor|maker))\s+(?=" + nm + ")", False),
    ]
    # 標題和內文分開比對，否則會把標題尾巴和內文開頭接成一句
    texts = [t for it in mine for t in (it["title"], it["summary"])]
    for t in texts:
        for pat, strip_de in pats:
            b = re.search(pat, t, re.I)
            if not b:
                continue
            biz = b.group(1).strip("，, ")
            if strip_de:                     # 「即将登陆马股创业板的柔佛建筑工程承包商」→「柔佛建筑工程承包商」
                biz = re.sub(r"^.*的", "", biz)
            else:                            # 「Butterfield Newly listed beverage blend maker」→「beverage blend maker」
                ws = biz.split()
                while ws and (ws[0].lower() in names or ws[0].lower() in
                              ("newly", "listed", "newly-listed", "the", "a", "an", "local", "leading")):
                    ws.pop(0)
                biz = " ".join(ws)
            if len(biz) >= 4:
                out["business"] = biz
                break
        if "business" in out:
            break
    if "business" not in out and mine:
        def score(i):
            sc = 2 if re.search(r"[一-鿿]", i["summary"]) else 0
            if re.search(r"业务|業務|供应|供應|制造|製造|工程|provider|manufactur|engaged|involved|"
                         r"principally|specialis|IPO|首次公开|首次公開", i["title"] + i["summary"], re.I):
                sc += 3
            return sc
        # 全大寫的是公告標題（法律文件），不是介紹
        mine = [i for i in mine if sum(c.isupper() for c in i["summary"][:80]) < 40] or mine
        best = max(mine, key=score)
        body = re.sub(r"^[（(][^）)]{2,16}[讯訊][）)]|^[A-Z][A-Z ]{3,30}:\s*", "", best["summary"])
        first = re.split(r"(?<=[。！？])|(?<=\.)\s", body)[0].strip()
        if first:
            out["about"] = first[:120]
    if re.search(r"ACE Market|创业板|創業板", blob):
        out["board_hint"] = "ACE Market"
    elif re.search(r"Main Market|主板|主要板", blob):
        out["board_hint"] = "Main Market"
    out["news"] = [{k: i[k] for k in ("title", "url", "time", "source")} for i in items[:3]]
    return out


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
    # 網頁上看得到的那幾檔（即將上市全部、最近上市前 6 檔）補上詳細資料
    for it in up[:8] + recent[:6]:
        it.update(ipo_detail(it["code"], it["short"], it.get("name", "")))
        if not it.get("board") and it.get("board_hint"):
            it["board"] = it["board_hint"]
        it["board_zh"] = board_zh(it.get("board"))
        # 上市後總股數 × IPO 價 = 上市市值；klsescreener 的 Issue Size 是總股數
        if it.get("issue_size") and not it.get("mcap") and it.get("ipo_price"):
            it["mcap"] = it["issue_size"] * it["ipo_price"]
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
