#!/usr/bin/env python3
"""從 stockanalysis.com 抓 Bursa 個股基本面（P/E、P/B、ROE、殖利率、派息率、分析師目標價）。

Yahoo 的 quoteSummary / v7 quote 端點已經需要認證，抓不到基本面，
所以這裡改用 stockanalysis.com 的 SvelteKit __data.json。
這個模組是「盡力而為」——抓不到就回傳空 dict，不會讓整個更新失敗。
"""

import json
import ssl
import urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
# 馬股在 /quote/klse/<代號>/，美股在 /stocks/<代號>/。
# watchlist 用 "us:NVDA" 這種前綴指定市場，沒前綴就當馬股。
BASE_KLSE = "https://stockanalysis.com/quote/klse/{t}/{page}__data.json"
BASE_US = "https://stockanalysis.com/stocks/{t}/{page}__data.json"


def _url(ticker, page):
    if ticker.lower().startswith("us:"):
        return BASE_US.format(t=ticker[3:], page=page)
    return BASE_KLSE.format(t=ticker, page=page)


def _resolve(arr, idx, depth=0):
    """SvelteKit 把資料壓成扁平陣列 + 索引指標，這裡還原成一般物件。"""
    if depth > 14 or idx is None or idx == -1:
        return None
    v = arr[idx]
    if isinstance(v, dict):
        return {k: _resolve(arr, i, depth + 1) for k, i in v.items()}
    if isinstance(v, list):
        return [_resolve(arr, i, depth + 1) for i in v]
    return v


def _get(ticker, page=""):
    req = urllib.request.Request(_url(ticker, page), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25, context=ssl.create_default_context()) as r:
        raw = json.loads(r.read().decode("utf-8"))
    out = []
    for node in raw.get("nodes", []):
        if node.get("type") != "data":
            continue
        obj = _resolve(node["data"], 0)
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _pick_rows(stats_obj, section):
    """statistics 頁的每個區塊是 {'text':..., 'data':[{'id','title','value'}]}"""
    sec = (stats_obj or {}).get(section) or {}
    rows = sec.get("data") or []
    return {r.get("id"): r.get("value") for r in rows if isinstance(r, dict)}


def fetch(ticker):
    data = {}

    # --- 主頁：本益比、殖利率、派息、分析師 ---
    try:
        for obj in _get(ticker):
            if "peRatio" in obj or "dividendYield" in obj:
                data.update({
                    "market_cap": obj.get("marketCap"),
                    "pe_ratio": obj.get("peRatio"),
                    "forward_pe": obj.get("forwardPE"),
                    "eps": obj.get("eps"),
                    "dps": obj.get("dps"),
                    "div_yield": obj.get("dividendYield"),
                    "payout_ratio": obj.get("payoutRatio"),
                    "ex_div_date": obj.get("exDivDate"),
                    "earnings_date": obj.get("earningsDate"),
                    "analyst_rating": obj.get("analysts"),
                    "analyst_target": obj.get("target"),
                    "beta": obj.get("beta"),
                })
                break
    except Exception:
        pass

    # --- statistics 頁：P/B、ROE、每股淨值（銀行股最關鍵的幾個） ---
    try:
        for obj in _get(ticker, "statistics/"):
            if "valuation" not in obj:
                continue
            ratios = _pick_rows(obj, "ratios")
            eff = _pick_rows(obj, "financialEfficiency")
            pos = _pick_rows(obj, "financialPosition")
            bal = _pick_rows(obj, "balanceSheet")
            divs = _pick_rows(obj, "dividends")
            data.update({
                "pb_ratio": ratios.get("pb"),
                "ptbv_ratio": ratios.get("ptbvRatio"),
                "ps_ratio": ratios.get("ps"),
                "peg_ratio": ratios.get("pegRatio"),
                "book_value": bal.get("equity"),
                "bvps": bal.get("bvps"),
                "roe": eff.get("roe"),
                "roa": eff.get("roa"),
                "debt_equity": pos.get("debtEquity"),
                "dividend_growth": divs.get("dividendGrowth"),
                "dividend_growth_years": divs.get("dividendGrowthYears"),
                "earnings_yield": divs.get("earningsYield"),
            })
            break
    except Exception:
        pass

    return {k: v for k, v in data.items() if v not in (None, "", "n/a")}


if __name__ == "__main__":
    import sys
    print(json.dumps(fetch(sys.argv[1]), indent=2, ensure_ascii=False))
