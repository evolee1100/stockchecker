#!/usr/bin/env python3
"""股息體檢：銀行股與 REIT 的十年派息紀錄，整理成收息族真正會問的幾個問題。

  1. 現在買，一年領多少？        近 12 個月派息（網頁用最新股價現算股息率）
  2. 一年派幾次、幾月除息？      近 12 個月的派息次數、近兩年除息的月份
  3. 股息是在變多還是變少？      近三年平均每年派息 vs 再前三年
  4. 現在的股息率算高還是低？    過去五年每週的股息率分布（20%／50%／80% 分位）
  5. 股息率回到歷史偏高的水準，價格是多少？  ← 收息族的另一種參考買價

為什麼「趨勢」用三年平均而不是逐年比：Yahoo 的派息是按除息日記的，同一筆股息
今年 12 月除息還是明年 1 月除息，會讓某一年多一次、下一年少一次。Axis REIT 2023 年
記到 4 次、2024 年 5 次，逐年比會變成「先減息 38% 再加息 60%」，其實每年都差不多。
三年加總把跨年的位移吸收掉。

為什麼股息率用「近 12 個月」：這是券商、新聞、stockanalysis 都在用的算法，
使用者拿去對才對得起來。逐週的股息率分布也用同一套算法。

刻意不做的事：不預測下一次派多少。REIT 派得比帳面盈利多很常見（盈利扣了建築物折舊），
可分派收入才是真正的來源，這裡沒有那個數字，所以網頁上要明講去查。
"""

from datetime import date, datetime, timedelta


def _pct(v, q):
    """v 已排序，q 介於 0–1，線性內插的分位數。"""
    if not v:
        return None
    k = (len(v) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def _d(iso):
    return datetime.strptime(iso, "%Y-%m-%d").date()


def _sum(divs, start, end):
    """(start, end] 之間除息的派息加總與次數。"""
    xs = [d["amount"] for d in divs if start < _d(d["date"]) <= end]
    return sum(xs), len(xs)


def build(divs, weekly, today=None):
    """divs：十年派息 [{date, amount}]；weekly：十年週線 [{date, close}]。

    沒有派息紀錄回傳 None。跟現價有關的（現價股息率、離區間多遠）在網頁現算。
    """
    today = today or date.today()
    divs = sorted((d for d in (divs or []) if d.get("amount")), key=lambda d: d["date"])
    if not divs:
        return None
    weekly = [p for p in (weekly or []) if p.get("close")]
    # 資料從哪天開始：派息紀錄只有兩年的股票，不能拿「前三年」來比
    since = min([_d(divs[0]["date"])] + ([_d(weekly[0]["date"])] if weekly else []))

    yr = timedelta(days=365)
    ttm, n12 = _sum(divs, today - yr, today)

    # 每個日曆年的派息（完整年度最多 7 年，加今年以來）
    by = {}
    for d in divs:
        y = int(d["date"][:4])
        a, n = by.get(y, (0.0, 0))
        by[y] = (a + d["amount"], n + 1)
    first_full = since.year + (0 if since.month == 1 else 1)
    years = [{"y": y, "amt": round(by.get(y, (0, 0))[0], 4), "n": by.get(y, (0, 0))[1]}
             for y in range(max(first_full, today.year - 7), today.year)]
    if today.year in by:
        years.append({"y": today.year, "amt": round(by[today.year][0], 4),
                      "n": by[today.year][1], "ytd": True})

    # 股息趨勢：近三年平均 vs 再前三年平均
    avg3, _ = _sum(divs, today - 3 * yr, today)
    avg3 /= 3
    prev3 = None
    if since <= today - 6 * yr:
        prev3 = _sum(divs, today - 6 * yr, today - 3 * yr)[0] / 3
    trend, chg = "", None
    if prev3:
        chg = (avg3 / prev3 - 1) * 100
        trend = "up" if chg >= 5 else ("down" if chg <= -5 else "flat")

    # 除息月份：看近兩年，有出現過的月份都列（一年派四次的通常每年同一批月份）
    months = sorted({int(d["date"][5:7]) for d in divs if _d(d["date"]) > today - 2 * yr})

    # 過去五年每週的股息率（當週收盤 ÷ 當時的近 12 個月派息）
    band = None
    pts = [p for p in weekly if _d(p["date"]) > today - 5 * yr]
    ys = []
    for p in pts:
        t = _d(p["date"])
        # 每週往回看 12 個月；派息紀錄開始還不滿一年的那幾週會少算，跳過
        if t - yr < since:
            continue
        a, _ = _sum(divs, t - yr, t)
        if a > 0:
            ys.append(a / p["close"] * 100)
    if len(ys) >= 100:
        ys.sort()
        band = {"lo": round(_pct(ys, 0.2), 2), "med": round(_pct(ys, 0.5), 2),
                "hi": round(_pct(ys, 0.8), 2), "weeks": len(ys)}

    return {
        "ttm": round(ttm, 4),
        "n12": n12,
        "months": months,
        "years": years,
        "avg3": round(avg3, 4),
        "prev3": round(prev3, 4) if prev3 else None,
        "trend": trend,
        "chg": round(chg, 1) if chg is not None else None,
        "band": band,
    }


if __name__ == "__main__":
    # python3 dividend.py 5106.KL：直接向 Yahoo 抓十年資料印出來，不改任何檔案
    import json
    import sys
    import urllib.parse
    import fetch
    for sym in sys.argv[1:] or ["5106.KL"]:
        raw = fetch.http_json("https://query1.finance.yahoo.com/v8/finance/chart/{}"
                              "?range=10y&interval=1wk&events=div".format(urllib.parse.quote(sym)))
        res = raw["chart"]["result"][0]
        wk = [{"date": datetime.fromtimestamp(t, fetch.MYT).strftime("%Y-%m-%d"), "close": c}
              for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]) if c]
        dv = [{"date": datetime.fromtimestamp(v["date"], fetch.MYT).strftime("%Y-%m-%d"),
               "amount": v["amount"]}
              for v in ((res.get("events") or {}).get("dividends") or {}).values()]
        print(sym, json.dumps(build(dv, wk), ensure_ascii=False, indent=1))
