#!/usr/bin/env python3
"""歷史低點參考價：根據歷年低點，算出分批買進的參考價位。

使用者的問題是「這檔跌到多少可以考慮買？」。這裡用最容易驗證的素材回答：
過去每一年的最低收盤價。全部是規則計算，任何人拿 Yahoo 的週線都算得出一樣的數字。

算法（價格一律用收盤價，不用盤中最低——盤中一根長下影線常常只成交幾手，
把它當支撐會讓參考價低到不可能買到；這跟 analyze.py「創 52 週新低」用收盤是同一個理由）：

  第一批  一年低點          過去 52 週的最低收盤。最近一次市場願意承接的價位
  第二批  近三年低點中位數  前兩個完整年度＋今年以來，三個「年度最低」取中位數  ← 參考買價
  第三批  五年最低          近五個完整年度＋今年以來的最低收盤。壞情境

另外兩個輔助判斷：

  年度回檔  過去五個完整年度，每年從年內高點最深跌多少，取中位數，套在一年高點上。
            長期上漲的股票，三年前的低點可能再也等不到，這個數字回答「正常的一年會回檔到哪」。
  趨勢      近三個完整年度＋今年的年度低點：一年比一年高是「墊高」，一年比一年低是「下移」。
            下移時歷史低點不是支撐，而是一路被跌破的價位——低點法在這種股票上不適用，要明白講。

刻意不做的事：不預測、不說「一定會跌到」或「不會再跌」。只說歷史上發生過什麼。
"""

from datetime import date

# 只算銀行股與 REIT：收息股的股價圍著獲利與股息走，歷年低點有參考意義。
# 題材股（AI 馬股、AI 美股）幾年漲好幾倍，三年前的低點離現價一半以上，算出來沒有用；
# 指數買不到、匯率與殖利率不是拿來買的，加密貨幣與毛棕櫚油也不適用
SECTORS = ("銀行", "REIT")

TIER_LABELS = ("一年低點", "近三年低點中位數", "五年最低")


def applicable(stock):
    return stock.get("sector") in SECTORS and not stock.get("is_index")


def _median(v):
    v = sorted(v)
    n = len(v)
    return None if not n else (v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2)


def _round(v):
    a = abs(v)
    return round(v, 2 if a >= 100 else (3 if a >= 1 else 4))


def _merge(weekly, daily):
    """長期用週線，近一年用日線（比較準）。兩段在日線的第一天接起來。"""
    if not daily:
        return list(weekly)
    start = daily[0]["date"]
    return [p for p in weekly if p["date"] < start] + list(daily)


def yearly(points):
    """每個日曆年的最低收盤、最高收盤，以及該年第一個資料點的日期（判斷是不是不完整的年度）。"""
    out = {}
    for p in points:
        c = p.get("close")
        if c is None or c <= 0:
            continue
        y = int(p["date"][:4])
        r = out.get(y)
        if r is None:
            out[y] = {"low": c, "low_date": p["date"], "high": c, "first": p["date"]}
            continue
        if c < r["low"]:
            r["low"], r["low_date"] = c, p["date"]
        if c > r["high"]:
            r["high"] = c
    return out


def _max_drawdown(closes):
    """一段價格裡，從之前的高點最深跌了多少（正數，%）。"""
    peak, worst = None, 0.0
    for c in closes:
        peak = c if peak is None or c > peak else peak
        worst = max(worst, (1 - c / peak) * 100)
    return worst


def build(weekly, daily, today=None):
    """weekly：十年週線 [{date, close}]；daily：一年日線 [{date, close}]。

    回傳 dict，資料不足（不到三個完整年度）回傳 None。
    距離、股息率、P/B 這些跟現價有關的數字不在這裡算：網頁有盤中報價，
    在網頁上用最新價格現算，才不會跟面板上方的價格對不起來。
    """
    today = today or date.today()
    cur = today.year
    pts = _merge(weekly or [], daily or [])
    yrs = yearly(pts)
    # 十年前那一年通常只抓到幾個月：一月底之後才開始的年度不算完整，不拿來比
    first = min(yrs) if yrs else cur
    done = [y for y in sorted(yrs)
            if y < cur and (y != first or yrs[y]["first"][5:7] == "01")]
    if len(done) < 3:
        return None

    recent = [p for p in (daily or []) if p.get("close")][-252:]
    if not recent:
        return None
    lo52 = min(recent, key=lambda p: p["close"])
    hi52 = max(p["close"] for p in recent)

    ytd = yrs.get(cur)
    # 三個年度低點：前兩個完整年度＋今年以來。一月初今年還沒有交易日時，改用前三個完整年度
    lows3 = [yrs[y]["low"] for y in done[-2:]] + ([ytd["low"]] if ytd else [yrs[done[-3]]["low"]])
    ref = _median(lows3)

    five = done[-5:]
    pool5 = [(yrs[y]["low"], yrs[y]["low_date"]) for y in five] + (
        [(ytd["low"], ytd["low_date"])] if ytd else [])
    floor5 = min(pool5)
    # 「跌破近五年所有年度低點」只跟完整年度比：今年的低點就是現在這一波，跟自己比沒有意義
    floor_done = min(yrs[y]["low"] for y in five)
    ten = min(((r["low"], r["low_date"]) for y, r in yrs.items() if y in done or y == cur))

    # 趨勢：近三個完整年度＋今年，年度低點的方向。5% 是門檻，不然持平的股票也會被叫成墊高
    seq = [yrs[y]["low"] for y in done[-3:]] + ([ytd["low"]] if ytd else [])
    trend = ""
    if all(b >= a for a, b in zip(seq, seq[1:])) and seq[-1] > seq[0] * 1.05:
        trend = "up"
    elif all(b <= a for a, b in zip(seq, seq[1:])) and seq[-1] < seq[0] * 0.95:
        trend = "down"

    # 年度回檔：每一年從年內高點最深跌多少
    dds = []
    for y in five:
        closes = [p["close"] for p in pts if p["date"][:4] == str(y) and p.get("close")]
        if len(closes) >= 20:
            dds.append(_max_drawdown(closes))
    pull = None
    if len(dds) >= 3:
        pct = _median(dds)
        pull = {"pct": round(pct, 1), "high": _round(hi52), "p": _round(hi52 * (1 - pct / 100)),
                "years": len(dds)}

    return {
        "ref": _round(ref),
        "tiers": [
            {"k": TIER_LABELS[0], "p": _round(lo52["close"]), "d": lo52["date"]},
            {"k": TIER_LABELS[1], "p": _round(ref)},
            {"k": TIER_LABELS[2], "p": _round(floor5[0]), "d": floor5[1]},
        ],
        "floor_done": _round(floor_done),
        "ten": {"p": _round(ten[0]), "d": ten[1]},
        "trend": trend,
        "pullback": pull,
        # 歷年低點（只列完整年度＋今年以來），網頁畫成一排小字，讓人自己看得出規律
        "years": [{"y": y, "p": _round(yrs[y]["low"]), "d": yrs[y]["low_date"]}
                  for y in done[-10:]] + (
            [{"y": cur, "p": _round(ytd["low"]), "d": ytd["low_date"], "ytd": True}] if ytd else []),
        "as_of": recent[-1]["date"],
    }


def status(bz, price, ttm_div=None, bvps=None):
    """跟現價有關的判斷。網頁的 JS 也照這套算，改這裡要一起改 index.html 的 bzStatus。"""
    if not bz or not price:
        return None
    if price < bz["floor_done"]:
        kind = "broken"          # 跌破近五年每一個完整年度的低點
    elif bz["trend"] == "down":
        kind = "falling"
    elif price <= bz["ref"]:
        kind = "below"
    else:
        kind = "above"
    gap = (bz["ref"] / price - 1) * 100
    out = {"kind": kind, "gap": gap}
    if ttm_div:
        out["yield_at_ref"] = ttm_div / bz["ref"] * 100
    if bvps:
        out["pb_at_ref"] = bz["ref"] / bvps
    return out


# ---------------------------------------------------------------------------
# 命令列：python3 buyzone.py            直接向 Yahoo 抓最新價格，印出參考價總表
#         python3 buyzone.py 1155.KL    只看某幾檔（代碼或名稱片段）
# 不需要先跑 fetch.py，也不會改動任何檔案。
# ---------------------------------------------------------------------------

def _yahoo(sym, rng, interval):
    import urllib.parse
    from datetime import datetime
    import fetch
    raw = fetch.http_json("https://query1.finance.yahoo.com/v8/finance/chart/{}?range={}&interval={}"
                          "&events=div".format(urllib.parse.quote(sym), rng, interval))
    res = raw["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    pts = [{"date": datetime.fromtimestamp(t, fetch.MYT).strftime("%Y-%m-%d"), "close": c}
           for t, c in zip(res["timestamp"], q["close"]) if c is not None]
    divs = [d["amount"] for d in ((res.get("events") or {}).get("dividends") or {}).values()
            if d["date"] >= (datetime.now().timestamp() - 365 * 86400)]
    return pts, sum(divs)


def _cli(argv):
    import json
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    cfg = json.load(open(os.path.join(here, "watchlist.json"), encoding="utf-8"))
    stocks = [s for s in cfg["stocks"] if applicable(s)]
    if argv:
        stocks = [s for s in stocks if any(a == s["symbol"] or a.lower() in s["name"].lower()
                                           for a in argv)]
    word = {"broken": "⚠ 跌破五年低點，低點法不適用", "falling": "⚠ 低點逐年下移，低點法不適用",
            "below": "✓ 已低於參考價", "above": ""}
    print("{:<10} {:<22} {:>9} {:>9} {:>8} {:>9} {:>9} {:>9} {:>7}  {}".format(
        "代碼", "名稱", "現價", "參考買價", "距離", "一年低點", "三年中位", "五年最低", "息@參考", "判斷"))
    for s in stocks:
        try:
            daily, ttm = _yahoo(s["symbol"], "1y", "1d")
            weekly, _ = _yahoo(s["symbol"], "10y", "1wk")
        except Exception as exc:  # noqa: BLE001
            print("{:<10} 抓取失敗：{}".format(s["symbol"], exc))
            continue
        bz = build(weekly, daily)
        price = daily[-1]["close"] if daily else None
        st = status(bz, price, ttm)
        if not bz or not st:
            print("{:<10} {:<22} 資料不足".format(s["symbol"], s["name"][:22]))
            continue
        t = [x["p"] for x in bz["tiers"]]
        note = word[st["kind"]] or ("低點逐年墊高" if bz["trend"] == "up" else "")
        print("{:<10} {:<22} {:>9.3f} {:>9.3f} {:>+7.1f}% {:>9.3f} {:>9.3f} {:>9.3f} {:>6}  {}".format(
            s["symbol"], s["name"][:22], price, bz["ref"], st["gap"], t[0], t[1], t[2],
            "{:.1f}%".format(st["yield_at_ref"]) if st.get("yield_at_ref") else "—", note))


if __name__ == "__main__":
    import sys
    _cli(sys.argv[1:])
