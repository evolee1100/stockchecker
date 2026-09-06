#!/usr/bin/env python3
"""每次更新後自動挑出值得看的事，其餘不顯示。

這是規則判斷不是 AI：更新跑在 GitHub 的機器上，沒有模型可以呼叫。
好處是結果穩定、可預期、不會編造；限制是只認得下面列出的情況。

每條重點都要能回答「發生什麼事」和「這個數字代表什麼」，
只陳述事實與定義，不做買賣建議。
"""

import re

LEVELS = {"critical": 0, "warn": 1, "good": 2, "info": 3}

# 常見法人的簡稱，全名太長會把重點擠掉
SHORT = {
    "EMPLOYEES PROVIDENT FUND": "EPF", "Employees Provident Fund": "EPF",
    "KUMPULAN WANG PERSARAAN": "KWAP", "Kumpulan Wang Persaraan": "KWAP",
    "AMANAHRAYA": "AmanahRaya", "Amanahraya": "AmanahRaya",
    "PERMODALAN NASIONAL": "PNB", "Permodalan Nasional": "PNB",
    "LEMBAGA TABUNG HAJI": "朝聖基金局",
}


def _short(name):
    for k, v in SHORT.items():
        if k.lower() in (name or "").lower():
            return v
    return (name or "")[:22]


def _num(v):
    try:
        return float(str(v).replace("%", "").replace(",", ""))
    except Exception:
        return None


def _days_to(iso, today):
    """iso 是 YYYY-MM-DD，回傳距今天幾天（負數代表已過）。"""
    from datetime import date
    try:
        y, m, d = (int(x) for x in iso.split("-"))
        return (date(y, m, d) - today).days
    except Exception:
        return None


def analyse(stocks, today):
    # 比較基準是馬股大盤，不是任何一個國際指數
    bench = next((s for s in stocks if s.get("sector") == "大盤"), None)
    mkt = bench.get("change_pct_1d") if bench else None
    out = []

    def add(level, stock, what, why=""):
        out.append({"level": level, "symbol": stock["symbol"], "name": stock["name"],
                    "sector": stock.get("sector", ""), "what": what, "why": why})

    for s in stocks:
        # ---- 國際指數：只看波動幅度，門檻比個股低（指數本來就不太動）----
        if s.get("sector") == "國際":
            d1 = s.get("change_pct_1d")
            if d1 is None:
                continue
            u = s.get("unit", "")
            if s["symbol"] == "^TNX" and abs(d1) >= 2:
                add("warn" if d1 > 0 else "good", s,
                    "美債殖利率 {:+.2f}% 至 {:.2f}{}".format(d1, s["price"], u),
                    "殖利率上升時，REIT 這類靠配息的資產相對沒吸引力，資金容易流出；下降則相反。")
            elif s["symbol"] == "MYR=X" and abs(d1) >= 1:
                add("warn" if d1 > 0 else "info", s,
                    "馬幣{} {:+.2f}%".format("走貶" if d1 > 0 else "走升", d1),
                    "USD/MYR 上升代表馬幣貶值。貶值不利外資留在馬股，也推高進口成本。")
            elif abs(d1) >= 1.5:
                add("critical" if d1 <= -2.5 else ("warn" if d1 < 0 else "info"), s,
                    "{:+.2f}%".format(d1),
                    "國際指數單日波動超過 1.5% 不常見，隔日馬股開盤通常會反映。")
            continue

        if s.get("is_index"):
            continue
        f = s.get("fundamentals") or {}
        d1 = s.get("change_pct_1d")
        unit = s.get("unit", "")
        price = s.get("price")
        pstr = "{:,.3f}".format(price) if price and price < 1 else "{:,.2f}".format(price or 0)

        # ---- 價格 ----
        if s.get("at_52w_low"):
            add("critical", s, "創 52 週新低 {}{}".format(pstr, unit and " " + unit),
                "距 52 週高點 {:.1f}%。跌破前低代表過去一年在這個價位買進的人全部套牢。"
                .format(s.get("off_52w_high") or 0))
        elif s.get("high_52w") and price and abs(price - s["high_52w"]) < 1e-9:
            add("info", s, "創 52 週新高 {}".format(pstr), "一年來的最高價。")

        if d1 is not None and abs(d1) >= 3:
            add("critical" if d1 < 0 else "good", s,
                "單日{} {:+.2f}%".format("重挫" if d1 < 0 else "大漲", d1), "")
        elif d1 is not None and abs(d1) >= 2:
            add("warn", s, "單日{} {:+.2f}%".format("下跌" if d1 < 0 else "上漲", d1), "")

        # 相對大盤：個股自己跌不稀奇，跑輸大盤才是個股問題。
        # 只對馬股有意義——拿美股跟 KLCI 比是沒有意義的。
        if d1 is not None and mkt is not None and s.get("sector") != "AI 美股":
            gap = d1 - mkt
            if abs(gap) >= 2:
                add("warn" if gap < 0 else "info", s,
                    "{}大盤 {:.1f} 個百分點".format("跑輸" if gap < 0 else "跑贏", abs(gap)),
                    "大盤今日 {:+.2f}%，這檔 {:+.2f}%。差距這麼大通常是個股自己的事。"
                    .format(mkt, d1))

        vr = s.get("volume_ratio")
        if vr and vr >= 2 and d1 is not None and d1 < 0:
            add("critical", s, "爆量下跌，成交量是均量的 {:.1f} 倍".format(vr),
                "量增價跌代表有人急著出貨，不是零星賣單。")
        elif vr and vr >= 2:
            add("info", s, "成交量放大 {:.1f} 倍".format(vr), "")

        # ---- 行事曆 ----
        for key, label, why in (
            ("earnings_date", "財報", "財報前後價格波動通常會放大。"),
            ("ex_div_date", "除息", "除息當天股價會自動扣掉配息金額，那不是下跌。"),
        ):
            n = _days_to(f.get(key, ""), today)
            if n is not None and 0 <= n <= 7:
                add("info", s, "{} 天後{}（{}）".format(n, label, f[key]), why)
            elif key == "ex_div_date" and n is not None and -5 <= n < 0:
                add("info", s, "{} 天前剛除息（{}）".format(-n, f[key]), why)

        # ---- 新公告 ----
        # 大股東持股變動要先聚合再判斷：EPF、KWAP 這類法人天天在微調，
        # 一筆一筆列出來會洗版，而且 5 萬股對大型銀行根本不算事。
        moves = {"減持": [], "增持": []}
        for fl in (s.get("filings") or [])[:6]:
            n = _days_to(fl.get("date", ""), today)
            if n is None or n < -3:
                continue
            body = fl.get("body_zh") or ""
            act = "減持" if "減持" in body else "增持" if "增持" in body else None
            if act:
                m = re.search(r"(減持|增持)\s*([\d,]+)\s*股", body)
                shares = _num(m.group(2)) if m else 0
                who = body.split(" 於 ")[0].strip()
                moves[act].append((who, shares or 0))
            else:
                add("info", s, "新公告：{}".format(fl.get("title_zh", "")[:46]), "")

        for act, rows in moves.items():
            if not rows:
                continue
            total = sum(x[1] for x in rows)
            if total < 5_000_000:          # 低於 500 萬股視為例行調整，不列
                continue
            names = "、".join(_short(w) for w, _ in rows[:2])
            more = "等 {} 筆".format(len(rows)) if len(rows) > 2 else ""
            add("warn" if act == "減持" else "info", s,
                "大股東{} {:,.0f} 股".format(act, total),
                "{}{}。這是法人申報的持股異動，不必然代表看法改變。".format(names, more))

        # ---- 估值 ----
        pb, roe, payout, yld = (_num(f.get("pb_ratio")), _num(f.get("roe")),
                                _num(f.get("payout_ratio")), _num(f.get("div_yield")))
        if pb is not None and roe is not None and pb < 1 and roe >= 10:
            add("good", s, "P/B {:.2f} 但 ROE {:.1f}%".format(pb, roe),
                "股價低於帳面淨值，同時股東權益報酬率有雙位數——這個組合不常見。")
        if payout is not None and payout > 100:
            # REIT 的派息率天生就高（折舊要加回去），一般公司超過 100% 才是警訊
            why = ("配息高於當年盈餘。REIT 因為要把折舊加回去，結構上就會偏高，"
                   "但緩衝空間小。" if s.get("sector") == "REIT"
                   else "配息金額高於當年賺到的盈餘，等於在吃老本或舉債配息。")
            add("warn", s, "派息率 {:.0f}%".format(payout), why)
        if yld is not None and yld >= 7:
            add("info", s, "殖利率 {:.2f}%".format(yld), "")

        # ---- 油棕 ----
        if s.get("avg30"):
            m1 = s.get("change_pct_1m")
            if m1 is not None and abs(m1) >= 3:
                add("warn" if m1 < 0 else "good", s,
                    "CPO 一個月{} {:+.2f}%".format("下跌" if m1 < 0 else "上漲", m1),
                    "{} 日均價 RM {:,.2f}/噸，鮮果串收購價會跟著動。"
                    .format(s.get("avg30_days", 30), s["avg30"]))

    out.sort(key=lambda x: LEVELS.get(x["level"], 9))

    # 每檔最多兩條，免得一檔股票把整個重點區佔滿
    seen, kept = {}, []
    for h in out:
        if seen.get(h["symbol"], 0) >= 2:
            continue
        seen[h["symbol"]] = seen.get(h["symbol"], 0) + 1
        kept.append(h)
    return kept[:10]


if __name__ == "__main__":
    import json, os
    from datetime import date
    here = os.path.dirname(os.path.abspath(__file__))
    d = json.load(open(os.path.join(here, "data", "data.json"), encoding="utf-8"))
    for h in analyse(d["stocks"], date.today()):
        print("[{}] {} — {}".format(h["level"], h["name"], h["what"]))
        if h["why"]:
            print("      {}".format(h["why"]))
