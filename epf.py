#!/usr/bin/env python3
"""EPF（僱員公積金局）每天在 Bursa 公告的買賣，整理成「買了什麼、賣了什麼」。

資料：EPF 持有一家上市公司 5% 以上時，每次持股變動都要公告（公司法 2016 第 138 條，
交易後 3 天內）；新進 5% 是第 137 條、降到 5% 以下是第 139 條。klsescreener 把 Bursa 的
股權變動公告整理成清單（網站本來就用它抓個股公告），這裡翻清單、只挑 EPF 的，再讀每則內文。

一則公告常有好幾筆：不同交易日、不同託管帳戶（一般帳戶、伊斯蘭 -IC、不同的 nominees），
買和賣也可能混在一起。全部逐筆記下，同一檔同一天合併成一筆淨買賣。

金額是估的：公告只寫股數，不寫成交價，用「股數 × 交易日收盤價」（Yahoo 日線）估算。

存檔 data/epf.json（進版控，每天累積，保留 90 天）：
  {"filings": {公告編號: {...}}, "updated_at": ...}
網頁用的摘要（summary()）放進 data.js 的 "epf"。

python3 epf.py            增量更新（翻到上次抓過的地方為止）
python3 epf.py --days 30  補抓過去 30 天（第一次用）
"""

import json
import os
import re
import sys
import time
import html as _html
import urllib.parse
from datetime import date, datetime, timedelta, timezone

import news

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "data", "epf.json")
MYT = timezone(timedelta(hours=8))
LIST = "https://www.klsescreener.com/v2/announcements?category=SH,CHSH&page={page}"
VIEW = "https://www.klsescreener.com{path}"
KEEP_DAYS = 90
WHO = "EMPLOYEES PROVIDENT FUND"
MON = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}


def _d(s):
    """'17 Nov 2026' → '2026-11-17'"""
    m = re.match(r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4})", s.strip())
    return "{}-{:02d}-{:02d}".format(m.group(3), MON[m.group(2).title()], int(m.group(1))) if m else None


def _num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except Exception:  # noqa: BLE001
        return None


def _flat(raw):
    t = re.sub(r"<script.*?</script>", " ", raw, flags=re.S)
    t = re.sub(r"<[^>]+>", " | ", t)
    return re.sub(r"(\s*\|\s*)+", " | ", _html.unescape(t))


def list_page(page):
    """公告清單的一頁：[(公告編號, 路徑, 公告日 'DD Mon', 標題)]"""
    h = json.loads(news._fetch(LIST.format(page=page), ajax=True)).get("html") or ""
    out = []
    for x in re.split(r'(?=<a href="/v2/announcements/view/)', h)[1:]:
        href = re.search(r'href="(/v2/announcements/view/(\d+))"', x)
        title = re.search(r'<div class="title">\s*(.*?)\s*</div>', x, re.S)
        day = re.search(r'<span class="day">(\d+)</span>\s*<span class="month">(\w+)</span>', x)
        if href and title:
            out.append((href.group(2), href.group(1), day and "{} {}".format(day.group(1), day.group(2)),
                        _html.unescape(title.group(1)).strip()))
    return out


def parse(path):
    """讀一則公告內文。回傳 dict，讀不懂回傳 None。"""
    t = _flat(news._fetch(VIEW.format(path=path)))
    g = lambda pat: (re.search(pat, t) or [None, None])[1]
    rows = []
    for d, sh, typ in re.findall(r"\| (\d{1,2} [A-Z][a-z]{2} \d{4}) \| ([\d,]+) \| (Acquired|Disposed|Others|[A-Z][a-z]+) \|", t):
        n = _num(sh) or 0
        sign = 1 if typ == "Acquired" else -1 if typ == "Disposed" else 0
        if sign:
            rows.append({"d": _d(d), "sh": sign * n})
    if not rows:
        return None
    pct = sum(_num(g(r"\| " + k + r" \(%\) \| ([\d.,]+) \|")) or 0
              for k in ("Direct", "Indirect/deemed interest"))
    return {
        "short": (g(r"\| Stock Name \| ([^|]+?) \|") or "").strip(),
        "company": (g(r"\| Company Name \| ([^|]+?) \|") or "").strip(),
        "announced": _d(g(r"\| Date Announced \| ([^|]+?) \|") or ""),
        "after": _num(g(r"\| Total no of securities after change \| ([\d,]+) \|")),
        "pct": round(pct, 3) if pct else None,
        "rows": rows,
    }


def load():
    try:
        with open(STORE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {"filings": {}}


def save(st):
    cut = (datetime.now(MYT).date() - timedelta(days=KEEP_DAYS)).isoformat()
    st["filings"] = {k: v for k, v in st["filings"].items() if (v.get("announced") or "9") >= cut}
    st["updated_at"] = datetime.now(MYT).strftime("%Y-%m-%d %H:%M")
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    with open(STORE, "w", encoding="utf-8") as fh:
        json.dump(st, fh, ensure_ascii=False, indent=0, sort_keys=True)


def update(days=None, max_pages=None, codes=None, log=print):
    """翻公告清單，把還沒抓過的 EPF 公告讀進來。

    days：往回翻幾天（補抓用）。沒給就翻到「上次最新公告日的前一天」為止（增量）。
    codes：{公司全名大寫: Bursa 代碼}，對不到代碼時用（內文的股票簡稱通常就夠）。
    """
    st = load()
    have = st["filings"]
    today = datetime.now(MYT).date()
    if days is None:
        last = max((v.get("announced") or "" for v in have.values()), default="")
        stop = (date.fromisoformat(last) - timedelta(days=1)) if last else today - timedelta(days=7)
    else:
        stop = today - timedelta(days=days)
    max_pages = max_pages or (25 if days is None else 12 * days)
    new, page = 0, 1
    while page <= max_pages:
        items = list_page(page)
        if not items:
            break
        oldest = None
        for aid, path, day, title in items:
            if day:
                dd, mm = day.split()
                y = today.year - (1 if MON[mm] > today.month else 0)
                oldest = date(y, MON[mm], int(dd))
            if WHO not in title.upper() or aid in have:
                continue
            kind = "new" if "137" in title else "ceased" if "139" in title else "change"
            try:
                f = parse(path)
            except Exception as exc:  # noqa: BLE001
                log("    ✗ EPF 公告 {} 讀不到：{}".format(aid, exc))
                continue
            if not f:
                continue
            f["kind"] = kind
            have[aid] = f
            new += 1
            time.sleep(0.25)
        if oldest and oldest < stop:
            break
        page += 1
        time.sleep(0.3)
    log("  EPF：讀了 {} 頁清單，新增 {} 則公告（共 {} 則）".format(page, new, len(have)))
    save(st)
    return st


def _closes(code, cache):
    """Yahoo 日線收盤 {date: close}，估金額用。一次抓 6 個月，同一輪重複用。"""
    if code in cache:
        return cache[code]
    import fetch
    out = {}
    try:
        raw = fetch.http_json("https://query1.finance.yahoo.com/v8/finance/chart/{}.KL?range=6mo&interval=1d"
                              .format(urllib.parse.quote(code)), tries=2)
        res = raw["chart"]["result"][0]
        for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]):
            if c is not None:
                out[datetime.fromtimestamp(t, MYT).strftime("%Y-%m-%d")] = c
    except Exception:  # noqa: BLE001
        pass
    cache[code] = out
    return out


def _price_on(closes, d):
    """交易日的收盤；那天沒有（停牌、資料缺）就用之前最近一天，再沒有就用之後最近一天。"""
    if not closes:
        return None
    ks = sorted(closes)
    before = [k for k in ks if k <= d]
    return closes[before[-1]] if before else closes[ks[0]]


def summary(st, quotes=None, days=30):
    """網頁用：每檔股票近 days 天的逐日淨買賣（交易日為準）。

    quotes：market.quotes() 的結果（全市場報價），用來把股票簡稱對到代碼、拿名稱。
    """
    by_short = {q["short"].upper(): q for q in (quotes or []) if q.get("short")}
    by_name = {q["name"].upper(): q for q in (quotes or []) if q.get("name")}
    cut = (datetime.now(MYT).date() - timedelta(days=days + 10)).isoformat()
    stocks, cache = {}, {}
    for aid, f in sorted(st["filings"].items(), key=lambda kv: (kv[1].get("announced") or "", kv[0])):
        q = by_short.get((f.get("short") or "").upper()) or by_name.get((f.get("company") or "").upper()) or {}
        code = q.get("code")
        key = code or f.get("short")
        if not key:
            continue
        s = stocks.setdefault(key, {"code": code, "short": f.get("short") or q.get("short"),
                                    "name": f.get("company") or q.get("name"), "days": {}, "pct": [],
                                    "kinds": []})
        if f.get("pct") is not None:
            s["pct"].append([f.get("announced"), f["pct"]])
        if f.get("kind") != "change":
            s["kinds"].append([f.get("announced"), f["kind"]])
        for r in f["rows"]:
            if (r.get("d") or "") < cut:
                continue
            s["days"][r["d"]] = s["days"].get(r["d"], 0) + r["sh"]
    out = []
    for key, s in stocks.items():
        if not s["days"]:
            continue
        closes = _closes(s["code"], cache) if s["code"] else {}
        ev = []
        for d in sorted(s["days"]):
            sh = s["days"][d]
            if not sh:
                continue
            p = _price_on(closes, d)
            ev.append([d, int(sh), round(sh * p) if p else None])
        if not ev:
            continue
        out.append({"code": s["code"], "short": s["short"], "name": s["name"], "ev": ev,
                    "pct": s["pct"][-12:], "kinds": s["kinds"][-4:]})
        time.sleep(0.1 if s["code"] not in cache else 0)
    dates = sorted({e[0] for x in out for e in x["ev"]})
    return {"updated_at": st.get("updated_at"), "dates": dates, "stocks": out}


if __name__ == "__main__":
    days = None
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])
    st = update(days=days)
    if "--summary" in sys.argv:
        import market
        sm = summary(st, market.quotes())
        print(len(sm["stocks"]), "檔，交易日", sm["dates"][-5:])
        top = sorted(sm["stocks"], key=lambda x: -sum(e[2] or 0 for e in x["ev"]))
        for x in top[:8] + top[-5:]:
            print("  {:8s} {:>14,.0f} RM  {:>13,} 股  {}".format(
                x["short"] or "?", sum(e[2] or 0 for e in x["ev"]), sum(e[1] for e in x["ev"]), x["pct"][-1:]))
