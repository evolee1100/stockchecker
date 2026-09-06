#!/usr/bin/env python3
"""每檔股票一份重點整理。

把手上四種素材綜合成一段可讀的中文摘要：
  1. 價量數據      —— 有完整歷史，可以講清楚跌了多少、多久、量能如何
  2. Bursa 公告全文 —— 抓得到，第一手，數字最可靠
  3. 媒體報導標題   —— 只有標題（Google News 把內文網址藏起來了），所以歸納主題而非引述內容
  4. 基本面        —— P/B、ROE、殖利率、派息率

刻意不寫「看好/看壞」這類結論：素材不足以支撐，而且那是投資建議。
只把事實整理到位，判斷留給讀的人。
"""

import re

# 報導標題的主題分類。標題是英文（翻譯前）與中文（翻譯後）混合，兩邊關鍵字都收。
THEMES = [
    ("業績", ["net profit", "earnings", "results", "revenue", "quarter", "1h", "2q", "3q", "4q",
              "淨利", "業績", "財報", "收入", "季度", "上半年"]),
    ("派息", ["dividend", "distribution", "dpu", "payout", "sen per unit",
              "派息", "分派", "股息", "每單位"]),
    ("併購", ["acquire", "acquisition", "dispose", "disposal", "merger", "stake", "buy",
              "收購", "出售", "併購", "股權"]),
    ("評等", ["target price", "upgrade", "downgrade", "rating", "analyst", "buy", "outperform",
              "目標價", "調升", "調降", "評等", "分析師"]),
    ("營運", ["occupancy", "tenant", "lease", "outlet", "branch", "expansion", "launch",
              "出租", "租約", "租戶", "分行", "擴張", "推出"]),
    ("人事", ["appoint", "ceo", "chairman", "resign", "director",
              "委任", "執行長", "主席", "辭任", "董事"]),
    ("政策", ["opr", "bank negara", "budget", "tax", "regulation", "policy", "withholding",
              "利率", "預算", "稅", "監管", "政策", "預扣"]),
]


def _fmt(v, digits=2):
    if v is None:
        return "—"
    return "{:,.{d}f}".format(v, d=digits)


def _price(v):
    if v is None:
        return "—"
    return _fmt(v, 3 if abs(v) < 1 else (0 if abs(v) >= 1000 else 2))


def _themes(articles):
    hits = {}
    for a in articles:
        blob = ((a.get("title") or "") + " " + (a.get("title_zh") or "")).lower()
        for name, keys in THEMES:
            if any(k in blob for k in keys):
                hits[name] = hits.get(name, 0) + 1
    return sorted(hits.items(), key=lambda x: -x[1])


def _sources(articles):
    seen = []
    for a in articles:
        src = (a.get("source") or "").replace(".com.my", "").replace(".com", "")
        src = {"theedgemalaysia": "The Edge", "thestar": "The Star",
               "nst": "NST", "NST Online": "NST", "EdgeProp": "EdgeProp",
               "freemalaysiatoday": "FMT"}.get(src, src)
        if src and src not in seen:
            seen.append(src)
    return seen


def build(stock):
    s = stock
    f = s.get("fundamentals") or {}
    unit = (" " + s["unit"]) if s.get("unit") else ""
    out = []

    # ---------- 價格 ----------
    bits = ["{} 收 {}{}".format(s.get("market_time", "")[:10], _price(s.get("price")), unit)]
    if s.get("change_pct_1d") is not None:
        bits.append("單日 {:+.2f}%".format(s["change_pct_1d"]))
    if s.get("at_52w_low"):
        bits.append("創 52 週新低")
    if s.get("volume_ratio") and s["volume_ratio"] >= 1.8:
        bits.append("成交量是均量的 {:.1f} 倍".format(s["volume_ratio"]))
    line = "、".join(bits) + "。"

    m1 = s.get("change_pct_1m")
    if m1 is not None:
        line += "一個月 {:+.2f}%".format(m1)
        # 一個月內若有除息，跌幅裡有一部分是機械性調整，不講清楚會誤判
        div = None
        for d in (s.get("dividends") or []):
            if d.get("date", "") >= s.get("series", [{}])[-22:][0].get("date", "9999"):
                div = d
        if div and s.get("price"):
            share = div["amount"] / s["price"] * 100
            line += "，其中 {} 除息 {} 佔約 {:.1f} 個百分點，不是實際下跌".format(
                div["date"], div["amount"], share)
        if s.get("change_pct_3m") is not None:
            line += "，三個月 {:+.2f}%".format(s["change_pct_3m"])
        line += "。"
    if s.get("off_52w_high") is not None and not s.get("is_index"):
        line += "距 52 週高點 {} 還有 {:.1f}%。".format(
            _price(s.get("high_52w")), abs(s["off_52w_high"]))
    out.append(("價格", line))

    # ---------- 官方公告 ----------
    fl = [x for x in (s.get("filings") or []) if x.get("body_zh")]
    if fl:
        rows = []
        for x in fl[:2]:
            # 只把連續空白收成一個，不能整個刪掉——刪了會把英文擠成 SentralREITManagement
            body = re.sub(r"\s+", " ", x["body_zh"]).strip()
            rows.append("{}：{}".format(x.get("date", ""), body[:130] +
                                       ("…" if len(body) > 130 else "")))
        out.append(("公告", " ".join(rows)))
    elif s.get("filings"):
        out.append(("公告", "最近 {} 則公告都沒有可抓取的內文，標題見下方清單。"
                    .format(len(s["filings"]))))

    # ---------- 媒體報導 ----------
    arts = s.get("articles") or []
    if arts:
        th = _themes(arts)
        src = _sources(arts)
        line = "近三個月 {} 則".format(len(arts))
        if th:
            line += "，主題集中在{}".format("、".join(n for n, _ in th[:3]))
        if src:
            line += "（{}）".format("、".join(src[:4]))
        line += "。標題見下方清單——媒體內文無法抓取，這裡只歸納主題。"
        out.append(("報導", line))

    # ---------- 數字 ----------
    nums = []
    for label, key in (("P/B", "pb_ratio"), ("P/E", "pe_ratio"), ("ROE", "roe"),
                       ("殖利率", "div_yield"), ("派息率", "payout_ratio"),
                       ("每股淨值", "bvps")):
        if f.get(key):
            nums.append("{} {}".format(label, f[key]))
    if f.get("analyst_target"):
        nums.append("分析師目標價 {}".format(f["analyst_target"]))
    if nums:
        out.append(("數字", "、".join(nums) + "。"))

    # ---------- 留意 ----------
    watch = []
    if s.get("at_52w_low"):
        watch.append("創 52 週新低，且伴隨爆量")
    def num(v):
        try: return float(str(v).replace("%", "").replace(",", ""))
        except Exception: return None
    payout, roe, pb = num(f.get("payout_ratio")), num(f.get("roe")), num(f.get("pb_ratio"))
    if payout and payout > 100:
        watch.append("派息率 {:.0f}%，高於當年盈餘".format(payout))
    if roe is not None and roe < 7 and not s.get("is_index"):
        watch.append("ROE 僅 {:.1f}%，賺錢效率偏低".format(roe))
    if pb is not None and roe is not None and pb < 1 and roe >= 10:
        watch.append("P/B 低於 1 但 ROE 有雙位數，估值與獲利不同步")
    if f.get("earnings_date"):
        watch.append("下次財報 {}".format(f["earnings_date"]))
    if watch:
        out.append(("留意", "；".join(watch) + "。"))

    return out


if __name__ == "__main__":
    import json, os, sys
    here = os.path.dirname(os.path.abspath(__file__))
    d = json.load(open(os.path.join(here, "data", "data.json"), encoding="utf-8"))
    want = sys.argv[1] if len(sys.argv) > 1 else "5123.KL"
    st = next(x for x in d["stocks"] if x["symbol"] == want or want in x["name"])
    print("===", st["name"], st["symbol"], "===")
    for k, v in build(st):
        print("【{}】{}".format(k, v))
