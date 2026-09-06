#!/usr/bin/env python3
"""每日抓取股票資料，寫入 data/data.json 供 index.html 讀取。

只用 Python 標準函式庫，不需要 pip install。
資料來源：Yahoo Finance chart API（免費、免金鑰）。
"""

import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import fundamentals
import news
import palm
import translate

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
MYT = timezone(timedelta(hours=8))  # 馬來西亞時間
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
    "?range=1y&interval=1d&events=div"
)


def http_json(url, tries=3):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - 想看到所有失敗原因
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("抓取失敗 {}: {}".format(url, last))


def pct(now, then):
    if now is None or then is None or then == 0:
        return None
    return round((now / then - 1) * 100, 2)


def fetch_one(stock):
    sym = stock["symbol"]
    raw = http_json(CHART_URL.format(sym=urllib.parse.quote(sym)))
    result = raw["chart"]["result"][0]
    meta = result["meta"]
    ts = result["timestamp"]
    quote = result["indicators"]["quote"][0]

    # 去掉沒有成交的空日子
    series = []
    for i, t in enumerate(ts):
        close = quote["close"][i]
        if close is None:
            continue
        series.append(
            {
                "date": datetime.fromtimestamp(t, MYT).strftime("%Y-%m-%d"),
                "close": round(close, 4),
                "volume": quote["volume"][i] or 0,
                "high": round(quote["high"][i], 4) if quote["high"][i] else None,
                "low": round(quote["low"][i], 4) if quote["low"][i] else None,
            }
        )

    closes = [p["close"] for p in series]
    vols = [p["volume"] for p in series]
    last = closes[-1] if closes else None

    def back(n):
        return closes[-1 - n] if len(closes) > n else None

    # 成交量相對過去 20 日均量，用來看「今天是不是有異常大單在賣」
    recent_vol = vols[-1] if vols else 0
    avg_vol_20 = sum(vols[-21:-1]) / 20 if len(vols) > 21 else None
    vol_ratio = round(recent_vol / avg_vol_20, 2) if avg_vol_20 else None

    divs = []
    for ev in (result.get("events", {}) or {}).get("dividends", {}).values():
        divs.append(
            {
                "date": datetime.fromtimestamp(ev["date"], MYT).strftime("%Y-%m-%d"),
                "amount": ev["amount"],
            }
        )
    divs.sort(key=lambda d: d["date"])
    ttm_div = round(sum(d["amount"] for d in divs[-4:]), 4) if divs else 0.0
    # 只計最近 12 個月的派息
    cutoff = (datetime.now(MYT) - timedelta(days=365)).strftime("%Y-%m-%d")
    ttm_div = round(sum(d["amount"] for d in divs if d["date"] >= cutoff), 4)

    hi52 = meta.get("fiftyTwoWeekHigh")
    lo52 = meta.get("fiftyTwoWeekLow")

    fund = {}
    if stock.get("sa"):
        fund = fundamentals.fetch(stock["sa"])

    feed = news.fetch(stock)

    # 英文資訊統一轉成中文；翻不出來的保留原文，兩邊都留著讓網頁可以切換
    for a in feed["articles"]:
        a["title_zh"] = translate.text_zh(a["title"])
    for i, f in enumerate(feed["filings"]):
        f["title_zh"] = translate.filing_title(f["title"])
        f["source_zh"] = translate.category(f["source"])
        # 只抓最近 3 則的內文：翻譯有成本，舊公告點開連結看就好
        if i < 3:
            body_en, body_zh = news.announcement_body(f["url"])
            if body_zh:                      # 持股變動類：規則解析出來的中文摘要
                f["body_zh"] = body_zh
            elif body_en:
                f["body"] = body_en
                f["body_zh"] = translate.paragraph_zh(body_en)

    if fund.get("analyst_rating"):
        fund["analyst_rating_zh"] = translate.rating(fund["analyst_rating"])
    for k in ("ex_div_date", "earnings_date"):
        if fund.get(k):
            fund[k] = translate.date_zh(fund[k])

    return {
        "symbol": sym,
        "sa": stock.get("sa"),
        "name": stock.get("name") or meta.get("shortName") or sym,
        "note": stock.get("note", ""),
        "sector": stock.get("sector", "其他"),
        "is_index": bool(stock.get("is_index")),
        "fundamentals": fund,
        "articles": feed["articles"],
        "filings": feed["filings"],
        "currency": meta.get("currency", ""),
        "price": round(last, 4) if last else None,
        "prev_close": back(1),
        "change_pct_1d": pct(last, back(1)),
        "change_pct_5d": pct(last, back(5)),
        "change_pct_1m": pct(last, back(21)),
        "change_pct_3m": pct(last, back(63)),
        "change_pct_1y": pct(last, closes[0] if closes else None),
        "day_high": meta.get("regularMarketDayHigh"),
        "day_low": meta.get("regularMarketDayLow"),
        "volume": recent_vol,
        "avg_volume_20d": int(avg_vol_20) if avg_vol_20 else None,
        "volume_ratio": vol_ratio,
        "high_52w": hi52,
        "low_52w": lo52,
        "off_52w_high": pct(last, hi52),
        "at_52w_low": bool(lo52 and last and abs(last - lo52) < 1e-9),
        "ttm_dividend": ttm_div,
        "yield_pct": round(ttm_div / last * 100, 2) if ttm_div and last else None,
        "dividends": divs[-6:],
        "market_time": datetime.fromtimestamp(
            meta["regularMarketTime"], MYT
        ).strftime("%Y-%m-%d %H:%M"),
        "series": series,  # 完整一年，網頁上可自由切區間
    }


def palm_oil():
    """棕櫚油：用 MPOB 官方每日牌價（RM/噸）。

    先前是拿 CME 的美元合約 CPO=F 乘匯率換算，那是估算值。
    MPOB 是馬來西亞棕油局的官方牌價，才是收購商和小園主實際參照的價格。
    """
    series = palm.daily_prices("6M")
    if not series:
        raise RuntimeError("MPOB 沒有回傳價格")

    closes = [p["close"] for p in series]
    last = closes[-1]

    def back(n):
        return closes[-1 - n] if len(closes) > n else None

    window = closes[-30:]
    avg30 = round(sum(window) / len(window), 2)

    feed = news.fetch({"q": "crude palm oil Malaysia", "symbol": "CPO"})
    for a in feed["articles"]:
        a["title_zh"] = translate.text_zh(a["title"])

    return {
        "symbol": "MPOB-CPO",
        "sa": None,
        "name": "毛棕櫚油 CPO",
        "note": "MPOB 官方每日馬來西亞毛棕櫚油牌價",
        "sector": "油棕",
        "is_index": False,
        "unit": "RM/噸",
        "calculator": True,
        "fundamentals": {},
        "articles": feed["articles"],
        "filings": [],
        "currency": "MYR",
        "price": last,
        "prev_close": back(1),
        "change_pct_1d": pct(last, back(1)),
        "change_pct_5d": pct(last, back(5)),
        "change_pct_1m": pct(last, back(21)),
        "change_pct_3m": pct(last, back(63)),
        "change_pct_1y": None,
        "day_high": None, "day_low": None,
        "volume": 0, "avg_volume_20d": None, "volume_ratio": None,
        "high_52w": round(max(closes), 2),
        "low_52w": round(min(closes), 2),
        "off_52w_high": pct(last, max(closes)),
        "at_52w_low": False,
        "ttm_dividend": 0, "yield_pct": None, "dividends": [],
        "avg30": avg30,
        "avg30_days": len(window),
        "market_time": series[-1]["date"],
        "series": series,
    }


def flags(s):
    """自動標出值得注意的訊號，讓你一眼看到「今天有事」。"""
    out = []
    if s.get("at_52w_low"):
        out.append({"level": "bad", "text": "創 52 週新低"})
    elif s.get("off_52w_high") is not None and s["off_52w_high"] <= -15:
        out.append(
            {"level": "warn", "text": "距 52 週高點 {:.1f}%".format(s["off_52w_high"])}
        )
    if s.get("change_pct_1d") is not None and abs(s["change_pct_1d"]) >= 2:
        lvl = "bad" if s["change_pct_1d"] < 0 else "good"
        out.append({"level": lvl, "text": "單日 {:+.2f}%".format(s["change_pct_1d"])})
    if s.get("volume_ratio") and s["volume_ratio"] >= 2:
        out.append({"level": "warn", "text": "成交量放大 {:.1f}x".format(s["volume_ratio"])})
    f = s.get("fundamentals") or {}

    def num(v):
        try:
            return float(str(v).replace("%", "").replace(",", ""))
        except Exception:
            return None

    pb, roe = num(f.get("pb_ratio")), num(f.get("roe"))
    if pb is not None and pb < 1:
        out.append({"level": "good", "text": "P/B {:.2f}（低於帳面淨值）".format(pb)})
    if roe is not None and roe >= 11:
        out.append({"level": "good", "text": "ROE {:.1f}%".format(roe)})
    elif roe is not None and roe < 7 and not s.get("is_index"):
        out.append({"level": "warn", "text": "ROE 偏低 {:.1f}%".format(roe)})
    payout = num(f.get("payout_ratio"))
    if payout is not None and payout > 100:
        out.append({"level": "warn", "text": "派息率 {:.0f}%（高於盈餘）".format(payout)})

    for d in s.get("dividends", []):
        days = (
            datetime.strptime(d["date"], "%Y-%m-%d").date()
            - datetime.now(MYT).date()
        ).days
        if -30 <= days <= 0:
            out.append(
                {"level": "info", "text": "{} 除息 {}".format(d["date"], d["amount"])}
            )
    return out


def main():
    with open(os.path.join(HERE, "watchlist.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)

    stocks, errors = [], []
    for stock in cfg["stocks"]:
        try:
            s = fetch_one(stock)
            s["flags"] = flags(s)
            stocks.append(s)
            f = s.get("fundamentals") or {}
            print("  ✓ {:11s} {:20.20s} {:>9} {:+7.2f}%  PB={:>5s} ROE={:>7s} 殖利率={:>7s}  新聞 {}/公告 {}".format(
                s["symbol"], s["name"], s["price"], s["change_pct_1d"] or 0,
                f.get("pb_ratio", "-"), f.get("roe", "-"), f.get("div_yield", "-"),
                len(s["articles"]), len(s["filings"])))
            time.sleep(0.4)  # 對資料來源客氣一點
        except Exception as exc:  # noqa: BLE001
            print("  ✗ {}: {}".format(stock["symbol"], exc))
            errors.append({"symbol": stock["symbol"], "error": str(exc)})

    try:
        po = palm_oil()
        po["flags"] = flags(po)
        stocks.append(po)
        print("  ✓ {:11s} {:20.20s} {:>9} {:+7.2f}%  30日均價 {}".format(
            po["symbol"], po["name"], po["price"], po["change_pct_1d"] or 0, po["avg30"]))
    except Exception as exc:  # noqa: BLE001
        print("  ✗ 棕櫚油: {}".format(exc))
        errors.append({"symbol": "MPOB-CPO", "error": str(exc)})

    payload = {
        "title": cfg.get("title", "股票追蹤"),
        "updated_at": datetime.now(MYT).strftime("%Y-%m-%d %H:%M:%S (MYT)"),
        "stocks": stocks,
        "errors": errors,
    }

    translate.save_cache()          # 把這輪新翻的句子寫回快取，明天就不用再翻

    os.makedirs(DATA_DIR, exist_ok=True)
    out = os.path.join(DATA_DIR, "data.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)

    # 同時輸出成 JS 檔，這樣 index.html 直接用瀏覽器開啟（file://）也能讀到資料
    with open(os.path.join(DATA_DIR, "data.js"), "w", encoding="utf-8") as fh:
        fh.write("window.STOCK_DATA = ")
        json.dump(payload, fh, ensure_ascii=False)
        fh.write(";\n")

    # 保留每日快照，之後可以回頭看歷史
    snap = os.path.join(DATA_DIR, "history")
    os.makedirs(snap, exist_ok=True)
    stamp = datetime.now(MYT).strftime("%Y-%m-%d")
    with open(os.path.join(snap, stamp + ".json"), "w", encoding="utf-8") as fh:
        json.dump(
            {"updated_at": payload["updated_at"],
             "stocks": [{k: v for k, v in s.items()
                         if k not in ("series", "articles", "filings")} for s in stocks]},
            fh, ensure_ascii=False, indent=1,
        )

    print("\n寫入 {} （{} 檔）".format(out, len(stocks)))


if __name__ == "__main__":
    main()
