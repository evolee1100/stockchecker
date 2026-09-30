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
    "Employees Provident Fd": "EPF",
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


def _chg(series, n):
    """series 最後一根對 n 根之前的漲跌幅（%）。"""
    if len(series) <= n:
        return None
    a, b = series[-1 - n]["close"], series[-1]["close"]
    return (b / a - 1) * 100 if a else None


def _clip(v, lim=15.0):
    return max(-lim, min(lim, v))


def _median(v):
    v = sorted(v)
    n = len(v)
    return None if not n else (v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2)


def _sector_sigma(members, days=250):
    """類股等權指數過去一年的日波動（%）。

    每個類股正常的波動差很多：銀行一天動 0.6% 算大，美股 AI 一天動 2% 很平常。
    用同一組固定門檻，高波動類股會天天觸發、看久了麻木，低波動類股卻要跌很慘才會被看見。
    """
    by_date = {}
    for m in members:
        ser = (m.get("series") or [])[-(days + 1):]
        for i in range(1, len(ser)):
            prev, cur = ser[i - 1]["close"], ser[i]["close"]
            if prev:
                by_date.setdefault(ser[i]["date"], []).append(_clip((cur / prev - 1) * 100))
    need = max(2, int(0.6 * len(members)))
    rets = [sum(v) / len(v) for d, v in sorted(by_date.items()) if len(v) >= need]
    if len(rets) < 60:
        return None
    mu = sum(rets) / len(rets)
    return (sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5


def _window_stats(members, days, upto):
    """某一天（upto）某個時間窗的類股統計：中位數、截尾平均、漲跌家數、各檔數值。"""
    vals = []
    for m in members:
        ser = [p for p in (m.get("series") or []) if p["date"] <= upto]
        # 單日只用「當天有交易」的成員，避免把兩天的報價混在一起算
        if days == 1 and (not ser or ser[-1]["date"] != upto):
            continue
        c = _chg(ser, days)
        if c is not None:
            vals.append((m, c))
    if len(vals) < max(4, int(0.6 * len(members))):
        return None
    raw = [c for _, c in vals]
    return {"med": _median(raw), "avg": sum(_clip(c) for c in raw) / len(raw),
            "down": sum(1 for c in raw if c < 0), "up": sum(1 for c in raw if c > 0),
            "k": len(raw), "vals": vals}


def _trend_entry(members, sigma, upto):
    """某一天是否「進入」一段類股趨勢。回傳 (方向, z, 時間窗名, 統計) 或 None。"""
    best = None
    for wname, days, zin in (("一個月", 21, 0.8), ("十日", 10, 1.0)):
        st = _window_stats(members, days, upto)
        if not st:
            continue
        z = st["med"] / (sigma * days ** 0.5)
        if z <= -zin and st["down"] >= 0.6 * st["k"]:
            cand = ("down", z, wname, st)
        elif z >= zin * 1.2 and st["up"] >= 0.6 * st["k"]:
            cand = ("up", z, wname, st)
        else:
            continue
        if best is None or abs(cand[1]) > abs(best[1]):
            best = cand
    return best


def _day_selloff(members, sigma, upto):
    """某一天整群同向大跌：中位數跌幅超過平常波動 1.5 倍、七成以上下跌。"""
    day = _window_stats(members, 1, upto)
    if not day:
        return False
    return day["med"] / sigma <= -1.5 and day["down"] >= 0.7 * day["k"]


def _down_active(members, sigma, d, dates):
    """某一天類股是否處於下跌狀態（含遲滯）。

    進入條件：一個月／十日趨勢觸發，或當天整群大跌。
    維持條件：過去十個交易日進入過，而且一個月中位數還在 -1.5% 以下、半數以上下跌。
    """
    e = _trend_entry(members, sigma, d)
    if (e and e[0] == "down") or _day_selloff(members, sigma, d):
        return True
    m1 = _window_stats(members, 21, d)
    # 維持條件用截尾平均而非中位數：已經進入過趨勢，極端值的風險低了，
    # 而中位數在「7 檔跌、4 檔平」時會接近零，讓警示在跌勢中途斷掉一天
    if not (m1 and m1["avg"] <= -1.5 and m1["down"] >= 0.5 * m1["k"]):
        return False
    i = dates.index(d)
    for dd in dates[max(0, i - 10):i]:
        e2 = _trend_entry(members, sigma, dd)
        if (e2 and e2[0] == "down") or _day_selloff(members, sigma, dd):
            return True
    return False


def _sector_trends(stocks, add):
    """類股整體趨勢，每個類股只出一條。

    為什麼要有這個：原本的規則全是「單一股票 × 單日 × 超過 2%」，對整個類股一起慢慢跌
    完全看不見。2026 年 9 月銀行股 11 檔一個月全數下跌、平均 -4.4%，卻沒有任何一天觸發。

    設計（兩輪稽核後）：
    - 判斷用中位數、顯示用「每檔截在 ±15%」的平均。Zetrix 一檔 -42% 曾把 AI 概念股
      平均拉到 -5.4%，拿掉它只剩 -1.3%——那是個股事件，不是類股趨勢。
    - 門檻依類股自己的波動換算（z 值）。銀行一天正常波動 0.76%、美股 AI 2.1%，
      同一組固定門檻會讓高波動類股天天觸發、低波動類股要跌很慘才被看見。
    - 遲滯：一旦進入下跌趨勢，就持續顯示到真的恢復為止（一個月中位數回到 -1.5% 以上，
      或下跌家數不到一半）。沒有遲滯時，數值在門檻附近遊走，銀行警示在跌勢中途
      斷斷續續消失，有一天甚至只剩綠色的「單日普漲」——正是使用者抱怨的「我卻不知道」。
    - 截掉的極端股不列入 named，讓它照常以個股身分被報出來。
    """
    groups = {}
    for st in stocks:
        if st.get("sector") in ("銀行", "REIT", "AI 馬股", "AI 美股"):
            groups.setdefault(st["sector"], []).append(st)

    label = {"銀行": "銀行股", "REIT": "REIT", "AI 馬股": "馬股 AI 概念股",
             "AI 美股": "美股 AI 概念股"}

    def short(nm):
        nm = re.sub(r"[（(].*?[）)]", "", nm)
        return re.sub(r"\s*[一-鿿].*$", "", nm).strip()

    named = set()
    for sec, members in groups.items():
        if len(members) < 4:
            continue
        sigma = _sector_sigma(members)
        if not sigma:
            continue
        dates = sorted({p["date"] for m in members for p in (m.get("series") or [])})
        if len(dates) < 25:
            continue
        today = dates[-1]

        entry = _trend_entry(members, sigma, today)
        holding = False
        down_now = _down_active(members, sigma, today, dates)
        if (not entry or entry[0] != "down") and down_now:
            # 遲滯：今天沒有新觸發，但下跌狀態還沒解除，用一個月的數字繼續報
            m1 = _window_stats(members, 21, today)
            if m1:
                entry = ("down", m1["med"] / (sigma * 21 ** 0.5), "一個月", m1)
                holding = True

        # 下跌已經持續幾個交易日。剛開始的要置頂，只是在持續的排後面——
        # 同一件事連報三週會變成壁紙，使用者就不看了
        days_on, LOOK = 0, 60
        if entry and entry[0] == "down":
            for d in reversed(dates[-LOOK:]):
                if not _down_active(members, sigma, d, dates):
                    break
                days_on += 1

        day = _window_stats(members, 1, today)
        dz = (day["med"] / sigma) if day else 0

        if entry:
            direction, z, wname, st = entry
            word = "下跌" if direction == "down" else "上漲"
            cnt = st["down"] if direction == "down" else st["up"]
            ordered = sorted(st["vals"], key=lambda x: x[1], reverse=(direction == "up"))
            top = [(m, c) for m, c in ordered if abs(c) <= 15][:2]
            extreme = [(m, c) for m, c in ordered if abs(c) > 15]

            what = ("{}平均 {:+.1f}%，{} 檔全數{}".format(wname, st["avg"], st["k"], word)
                    if cnt == st["k"] else
                    "{}平均 {:+.1f}%，{} 檔中 {} 檔{}".format(wname, st["avg"], st["k"], cnt, word))
            why = "{}最多：{}".format("跌" if direction == "down" else "漲",
                                    "、".join("{} {:+.1f}%".format(short(m["name"]), c)
                                             for m, c in top))
            if days_on >= LOOK:
                what += "（已持續超過 {} 個交易日）".format(LOOK)
            elif days_on > 1:
                what += "（持續第 {} 天）".format(days_on)
            if day and abs(dz) >= 1:
                why += "。今天{}，平均 {:+.2f}%".format(
                    "再跌" if day["avg"] < 0 else "反彈", day["avg"])
            if abs(z) >= 2:
                why += "。幅度是這個類股平常波動的 {:.1f} 倍".format(abs(z))
            if extreme:
                why += "（{} 單檔波動過大，另外列出）".format(
                    "、".join(short(m["name"]) for m, _ in extreme))
            worsening = bool(day and dz <= -1.0)
            sev = ("good" if direction == "up" else
                   "critical" if (z <= -1.6 or (day and dz <= -1.5)) else "warn")
            # 剛開始（五天內）或今天又明顯下跌 → 置頂；單純持續 → 排在新事件後面
            fresh = direction == "up" or days_on <= 5 or worsening
            add(sev, {"symbol": "SECTOR:" + sec, "name": label[sec], "sector": sec,
                      "last_bar": today}, what, why,
                kind="類股" if fresh else "類股持續", mag=abs(z) * 3)
            named.update(m["symbol"] for m, _ in top)

        elif day and abs(dz) >= 1.5 and (
                day["down"] if dz < 0 else day["up"]) >= 0.7 * day["k"]:
            # 沒有趨勢、但今天整群同向大動
            word = "普跌" if dz < 0 else "普漲"
            add("critical" if dz <= -2 else ("warn" if dz < 0 else "good"),
                {"symbol": "SECTOR:" + sec, "name": label[sec], "sector": sec,
                 "last_bar": today},
                "單日{}，平均 {:+.2f}%，{} 檔中 {} 檔{}".format(
                    word, day["avg"], day["k"], day["down"] if dz < 0 else day["up"],
                    "下跌" if dz < 0 else "上漲"),
                "", kind="類股", mag=abs(dz) * 2)
    return named



def analyse(stocks, today):
    # 比較基準是馬股大盤，不是任何一個國際指數
    bench = next((s for s in stocks if s.get("sector") == "大盤"), None)
    mkt = bench.get("change_pct_1d") if bench else None
    out = []
    named = set()          # 已經在類股那一條被點名的個股，個別就不重複報

    def add(level, stock, what, why="", when=None, kind="盤中", mag=0):
        """when 是這件事發生的日期（ISO），kind 說明它是哪種事。

        每條重點都必須帶日期。資料可能是前一個交易日的（例如週末開啟），
        沒有日期的話「今日重點」四個字會讓人以為是當下發生的。
        """
        # market_time 是把收盤時刻換算成馬來西亞時間，美股 16:00 ET 收盤
        # = 隔天 04:00 MYT，跨過午夜就整整差一天（MSFT 曾被標成週六）。
        # 價格類事件一律用最後一根 K 棒的日期。
        # fetch.py 沒有寫 last_bar 欄位，之前一直退回 market_time，
        # 美股於是都被標成隔天、而且是「盤中」。直接取價格序列最後一根的日期。
        series = stock.get("series") or []
        day = (when or stock.get("last_bar")
               or (series[-1].get("date") if series else "")
               or (stock.get("market_time") or "")[:10])
        # 資料是前一個交易日的收盤，標「盤中」會讓人以為是當下正在發生
        if kind == "盤中" and day != today.isoformat():
            kind = "收盤"
        out.append({"level": level, "symbol": stock["symbol"], "name": stock["name"],
                    "sector": stock.get("sector", ""), "what": what, "why": why,
                    "when": day, "kind": kind, "mag": mag})

    named |= _sector_trends(stocks, add)

    for s in stocks:
        # ---- 加密：門檻要比股票高很多 ----
        # 實測 BTC 近半年有 24% 的天數單日波動 ≥2%，用股票的門檻會天天跳提醒。
        # USDT 是錨定 1 美元的穩定幣，漲跌沒有意義，要看的是脫鉤。
        if s.get("sector") == "加密":
            d1 = s.get("change_pct_1d")
            price = s.get("price")
            if s["symbol"].startswith("USDT"):
                if price:
                    gap = (price - 1) * 100
                    if abs(gap) >= 0.5:
                        add("critical" if abs(gap) >= 2 else "warn", s,
                            "偏離 1 美元 {:+.2f}%（{:.4f}）".format(gap, price),
                            "穩定幣的價格本來就該貼著 1 美元。偏離超過 0.5% 代表市場對它的"
                            "兌付能力有疑慮，脫鉤時通常伴隨大量贖回。")
                continue
            if d1 is None:
                continue
            if abs(d1) >= 5:
                add("critical" if d1 < 0 else "good", s,
                    "單日{} {:+.2f}%".format("重挫" if d1 < 0 else "大漲", d1),
                    "5% 以上的單日波動即使對加密貨幣也算大。")
            elif abs(d1) >= 3:
                add("warn" if d1 < 0 else "info", s, "單日 {:+.2f}%".format(d1), "")
            continue

        # ---- 國際指數：只看波動幅度，門檻比個股低（指數本來就不太動）----
        if s.get("sector") == "國際":
            d1 = s.get("change_pct_1d")
            if d1 is None:
                continue
            u = s.get("unit", "")
            if s["symbol"] == "^TNX" and abs(d1) >= 2:
                add("warn" if d1 > 0 else "good", s,
                    "美債殖利率 {:+.2f}% 至 {:.2f}{}".format(d1, s["price"], u),
                    "公債殖利率是 REIT 這類配息資產的對照基準：殖利率走高時，"
                    "同樣的配息看起來就沒那麼吸引人。單日變動幅度通常不大，"
                    "要看的是方向持續性。")
            elif s["symbol"].endswith("MYR=X") or s["symbol"] == "MYR=X":
                if abs(d1) < 1:
                    continue
                other = "美元" if s["symbol"] == "MYR=X" else "新幣"
                add("warn" if d1 > 0 else "info", s,
                    "馬幣兌{}{} {:+.2f}%".format(other, "走貶" if d1 > 0 else "走升", d1),
                    "數字上升代表馬幣相對{}貶值。貶值不利外資留在馬股，也推高進口成本。"
                    .format(other))
            elif abs(d1) >= 1.5:
                add("critical" if d1 <= -2.5 else ("warn" if d1 < 0 else "info"), s,
                    "{:+.2f}%".format(d1),
                    "國際指數單日出現這個幅度時，隔日馬股開盤常被拿來對照，"
                    "但兩者不必然同向。")
            continue

        if s.get("is_index"):
            continue
        f = s.get("fundamentals") or {}
        d1 = s.get("change_pct_1d")
        unit = s.get("unit", "")
        price = s.get("price")
        pstr = "{:,.3f}".format(price) if price and price < 1 else "{:,.2f}".format(price or 0)

        # ---- 價格 ----
        # 只有「這個交易日才跌破」才算新聞。一檔股票在低點附近盤整一週，
        # 原本的寫法會連續七天都報「創新低」。
        # 原本比對 Yahoo 的 fiftyTwoWeekLow，那是盤中最低價，
        # 跟收盤價幾乎不可能完全相等，導致這條規則實際上從不觸發。
        # 改成直接看收盤序列：今天的收盤是不是這一年來最低。
        closes = [p["close"] for p in (s.get("series") or [])]
        fresh_low = (len(closes) > 5 and closes[-1] < min(closes[:-1]))
        fresh_high = (len(closes) > 5 and closes[-1] > max(closes[:-1]))
        if fresh_low:
            add("critical", s, "創 52 週新低 {}{}".format(pstr, unit and " " + unit),
                "距 52 週高點 {:.1f}%。跌破前低代表過去一年在這個價位買進的人全部套牢。"
                .format(s.get("off_52w_high") or 0))
        elif fresh_high:
            add("info", s, "創 52 週新高 {}".format(pstr), "一年來收盤的最高價。")

        if d1 is not None and abs(d1) >= 3:
            add("critical" if d1 < 0 else "good", s,
                "單日{} {:+.2f}%".format("重挫" if d1 < 0 else "大漲", d1), "", mag=abs(d1))
        elif d1 is not None and abs(d1) >= 2:
            add("warn", s, "單日{} {:+.2f}%".format("下跌" if d1 < 0 else "上漲", d1), "",
                mag=abs(d1))

        # 多日累積：每天只跌一點、一個月下來卻跌很多，單日規則永遠抓不到。
        # 已經在類股那一條被點名的就不重複。
        m1, d5 = s.get("change_pct_1m"), s.get("change_pct_5d")
        if s["symbol"] not in named and s.get("sector") not in ("油棕", "加密"):
            if m1 is not None and m1 <= -10:
                add("critical" if m1 <= -15 else "warn", s,
                    "一個月累計 {:+.1f}%".format(m1),
                    "不是單日暴跌，而是連續下跌累積出來的。", mag=abs(m1) / 2)
            elif d5 is not None and d5 <= -6:
                add("warn", s, "五日累計 {:+.1f}%".format(d5), "", mag=abs(d5) / 1.5)

        # 相對大盤：個股自己跌不稀奇，跑輸大盤才是個股問題。
        # 只對馬股有意義——拿美股跟 KLCI 比是沒有意義的。
        # 已經報過單日漲跌就不要再報一次相對大盤——那是同一件事的兩種說法。
        # 但個股只動 1%、大盤反向動 1.5% 這種情況沒有單日重點，
        # 相對表現才是唯一看得出來的訊號，所以規則本身要留著。
        already = any(o["symbol"] == s["symbol"] and "單日" in o["what"] for o in out)
        if (d1 is not None and mkt is not None and not already
                and s.get("sector") not in ("AI 美股", "油棕")):
            gap = d1 - mkt
            if abs(gap) >= 2:
                add("warn" if gap < 0 else "info", s,
                    "{}大盤 {:.1f} 個百分點".format("跑輸" if gap < 0 else "跑贏", abs(gap)),
                    "大盤今日 {:+.2f}%，這檔 {:+.2f}%。差距這麼大通常是個股自己的事。"
                    .format(mkt, d1))

        vr = s.get("volume_ratio")
        if vr and vr >= 2 and d1 is not None and d1 < 0:
            add("critical", s, "爆量下跌，成交量是均量的 {:.1f} 倍".format(vr),
                "成交量明顯高於近期均量，且收黑。放量本身只說明有大額成交，"
                "不必然是誰在急著賣。")
        elif vr and vr >= 2:
            add("info", s, "成交量放大 {:.1f} 倍".format(vr), "")

        # ---- 行事曆 ----
        for key, label, why in (
            ("earnings_date", "財報", "財報前後價格波動通常會放大。"),
            ("ex_div_date", "除息", "除息當天股價會自動扣掉配息金額，那不是下跌。"),
        ):
            n = _days_to(f.get(key, ""), today)
            # 日期標籤要用事件本身的日期，不能用最後成交日——
            # 否則文字寫「9/23 除息」、旁邊標籤卻寫 9/25，自相矛盾
            if n is not None and 0 <= n <= 3:
                add("info", s, "今天{}".format(label) if n == 0
                    else "{} 天後{}".format(n, label), why,
                    when=f[key], kind="行事曆")
            elif key == "ex_div_date" and n is not None and n == -1:
                # 只報除息當天的隔天。原本往前算五天、往後算七天，
                # 同一個除息日會連續十三天佔著名額
                add("info", s, "昨天除息", why, when=f[key], kind="行事曆")

        # ---- 新公告 ----
        # 大股東持股變動要先聚合再判斷：EPF、KWAP 這類法人天天在微調，
        # 一筆一筆列出來會洗版，而且 5 萬股對大型銀行根本不算事。
        # 大股東持股變動要按「公告日」分開統計。
        # 原本把最近幾天的公告全部加總成一個數字，等於把三天的事講成一件，
        # 而且沒有日期，看起來就像今天剛發生。
        by_day, seen_moves = {}, set()
        for fl in (s.get("filings") or [])[:10]:
            n = _days_to(fl.get("date", ""), today)
            if n is None or n < -5:
                continue
            body = fl.get("body_zh") or ""
            act = "減持" if "減持" in body else "增持" if "增持" in body else None
            if not act:
                if n >= -2:
                    add("info", s, "公告：{}".format(fl.get("title_zh", "")[:44]), "",
                        when=fl.get("date"), kind="公告")
                continue

            m = re.search(r"(減持|增持)\s*([\d,]+)\s*股", body)
            who = body.split(" 於 ")[0].strip()
            pm = re.search(r"佔\s*([\d.]+)\s*%", body)
            shares = _num(m.group(2)) if m else 0

            # 同一筆成交會被第138條（大股東）與第219條（董事）各公告一次，
            # 兩份的持有人、股數、變動後百分比完全相同。不先去重就會算兩倍。
            key = (re.sub(r"[^a-z0-9]", "", who.lower())[:28], act,
                   shares, pm.group(1) if pm else "")
            if key in seen_moves:
                continue
            seen_moves.add(key)

            # 董事本人買賣跟法人調整部位性質完全不同，不能用同一句話解釋
            is_dir = "董事" in (fl.get("title_zh") or "")
            by_day.setdefault((fl.get("date"), act), []).append((who, shares, is_dir))

        for (day, act), rows in sorted(by_day.items(), reverse=True)[:2]:
            total = sum(x[1] for x in rows)
            if total < 5_000_000:          # 低於 500 萬股視為例行調整，不列
                continue
            names = "、".join(_short(w) for w, _, _ in rows[:2])
            more = "等 {} 筆".format(len(rows)) if len(rows) > 2 else ""
            who_kind = "董事" if all(x[2] for x in rows) else "大股東"
            # 法人（公積金、信託、代理人）調整部位跟個人賣自家股票性質不同，
            # 說明不能一律寫成「法人申報」
            corp = ("BERHAD", "BHD", "FUND", "BOARD", "NOMINEES", "TRUSTEES",
                    "KUMPULAN", "PERBADANAN", "LEMBAGA", "SDN", "LIMITED", "LTD")
            personal = all(not any(c in w.upper() for c in corp) for w, _, _ in rows)
            why = ("{}{}。董事或個人本人買賣自家股票，跟法人調整部位性質不同。"
                   if (who_kind == "董事" or personal) else
                   "{}{}。這是法人申報的持股異動，不必然代表看法改變。")
            add("warn" if act == "減持" else "info", s,
                "{}{} {:,.0f} 股".format(who_kind, act, total),
                why.format(names, more), when=day, kind="公告")

        # ---- 估值 ----
        # 這裡刻意不放派息率、殖利率、P/B+ROE 這類「長期狀態」。
        # 它們每次更新都會成立，放進來等於十條裡固定佔掉好幾條，
        # 把真正今天發生的事擠掉。這些已經在每檔個股的【留意】欄位裡。

        # ---- 油棕 ----
        if s.get("avg30"):
            m1 = s.get("change_pct_1m")
            if m1 is not None and abs(m1) >= 3:
                add("warn" if m1 < 0 else "good", s,
                    "CPO 一個月{} {:+.2f}%".format("下跌" if m1 < 0 else "上漲", m1),
                    "{} 日均價 RM {:,.2f}/噸，鮮果串收購價會跟著動。"
                    .format(s.get("avg30_days", 30), s["avg30"]))

    # 排序：類股的大方向永遠排最前面（那是「不用花時間就知道」的核心），
    # 其次看嚴重程度，同級再比份量。原本只按等級排、沒有次要鍵，
    # 同級的名次其實就是追蹤清單的順序。
    # 只有「壞消息」的類股條目置頂：那是使用者最怕漏掉的。
    # 好消息的類股條目若也一律置頂，12 格常被它們佔掉，重要的個股警示反而被擠出去。
    # 「持續中」的類股下跌也要置頂（排在新出現的之後）：銀行股連跌 13 天、
    # 11 檔全數下跌，原本因為「不是新消息」排到第 7 條，首頁只顯示前 5 條就看不到了
    # ——這正是「銀行股跌很兇我卻不知道」的原因。類股最多 4 條，不會把個股擠光。
    def pin(x):
        if x["level"] not in ("critical", "warn"):
            return 2
        return {"類股": 0, "類股持續": 1}.get(x.get("kind"), 2)

    out.sort(key=lambda x: (pin(x), LEVELS.get(x["level"], 9), -(x.get("mag") or 0)))

    # 每檔最多兩條，免得一檔股票把整個重點區佔滿
    seen, kept = {}, []
    for h in out:
        if seen.get(h["symbol"], 0) >= 2:
            continue
        seen[h["symbol"]] = seen.get(h["symbol"], 0) + 1
        kept.append(h)
    return kept[:12]


if __name__ == "__main__":
    import json, os
    from datetime import date
    here = os.path.dirname(os.path.abspath(__file__))
    d = json.load(open(os.path.join(here, "data", "data.json"), encoding="utf-8"))
    for h in analyse(d["stocks"], date.today()):
        print("[{}] {} — {}".format(h["level"], h["name"], h["what"]))
        if h["why"]:
            print("      {}".format(h["why"]))
