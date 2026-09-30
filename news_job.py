#!/usr/bin/env python3
"""快速新聞更新：只抓新聞、公告、漲跌排行與追蹤股報價，寫成 news.json。

為什麼另外一支：
  fetch.py 一輪要好幾分鐘（逐檔抓一年股價、基本面、報導內文再翻譯），排程寫 3 小時一次，
  GitHub 實際上 1.6–6.5 小時才輪到一次。9/29 銀行股節節敗退，網頁上的新聞卻還是半天前的。
  這支每 5 分鐘排一次（實際約 12 分鐘一次），只做「有什麼新消息」，一分鐘內跑完，
  結果推到獨立的 news 分支，網頁直接從 GitHub API 讀，不必等 Pages 重新部署。

跟 fetch.py 的分工：
  - 不抓股價歷史、基本面、報導內文（點開時網頁會現翻）
  - 不寫 main 的任何檔案。翻譯快取 data/translations.json 只讀不寫——
    寫回 main 會跟 3 小時那支和 ./sync.sh 撞在一起
  - 改用上一輪的 news.json 當快取：標題一樣就沿用譯文，
    不然每 5 分鐘都要把幾百則標題重翻一次，翻譯服務很快就會限流

用法：python3 -u news_job.py --out _news
      （_news/news.json 若已存在，就是上一輪的結果，會被當成快取讀進來再覆寫）

來源掛掉時：
  - 某一塊抓不到（報價、快訊、某檔的公告或新聞），那一塊沿用上一輪的 news.json，
    不能發一份「比較空、時間卻比較新」的檔案，把上一輪好好的資料蓋掉
  - 大部分都抓不到（網路斷了、整個被擋），就不寫檔：上一輪的 news.json 原封不動，
    workflow 看檔案沒變就不推，網頁上「新聞 HH:MM 更新」也不會假裝剛更新過
"""

import argparse
import json
import os
import re
import ssl
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import market
import news
import translate

HERE = os.path.dirname(os.path.abspath(__file__))
MYT = timezone(timedelta(hours=8))

ARTICLES = 8        # 每檔新聞最多幾則；網頁會跟 data.js 那份合併後再依時間排
FILINGS = 12        # 每檔公告最多幾則，跟 fetch.py 一樣
HEADLINES = 8
BODIES = 30         # 每輪最多解析幾則「新的」持股變動公告（平行抓，30 則約 2 秒）；舊的沿用上一輪
# 同時是地名的簡稱：「Ombak KLCC 開幕」「在吉隆坡会展中心（KLCC）举行」講的是場地，
# 不是 KLCC 產託；KL Sentral 是車站，不是 Sentral REIT。只憑這些字不算提到這家公司
WEAK_NAMES = {"klcc", "sentral"}

# 公司名後面接這些字，講的是它的研究部門在評「別家」：「BIMB Securities 看好偉特」
# 被標成 Bank Islam 的新聞、「马银行投行」評的也不是馬銀行
BROKER_AFTER = r"(?!\s*(?:research|securities|investment|invest\b|ib\b|投行|研究|证券|證券|投资银行|投資銀行))"
# 標題有三個以上逗號／頓號的是多家公司的綜合報導（Stocks to watch、Insider Moves）
ROUNDUP_SEP = re.compile(r"[,、，]")
# 行情頁不是新聞（「CME Group 美元馬來西亞毛棕櫚油日曆」「期貨價格歷史記錄」）
PRICE_PAGE = re.compile(r"\b(calendar|price history|historical (data|prices?)|live chart|"
                        r"quotes? and chart|futures? quotes?)\b", re.I)

# 產業新聞：標題沒點名，但講的是整個產業（「债券回酬率急升 银行股节节败退」）。
# 只在 klsescreener 也標了這檔股票時才算，免得每則銀行新聞都塞進每一家銀行
SECTOR_WORDS = {
    "銀行": re.compile(r"银行|銀行|\bbank(s|ing)?\b|金融股|金融指数|金融指數", re.I),
    "REIT": re.compile(r"产托|產託|\bREITs?\b", re.I),
}

# 大盤綜合報導（「马股闭市重挫26.06点」）。它會標上當天提到的每一檔股票，
# 放進個股新聞會把真正講這家公司的消息擠掉，所以改放「市場快訊」
MARKET_ZH = re.compile(r"马股|馬股|综指|綜指|大马股市|大馬股市")
MARKET_EN = re.compile(r"\bKLCI\b")
CJK = re.compile(r"[一-鿿]")

# 持股變動類公告的標題（規則翻譯後），跟 fetch.py 的判斷一樣
HOLDING = ("持股變動", "權益變動")

# 翻譯請求最多等幾秒（translate.py 預設 20 秒，是給 3 小時那支用的）。
# 翻譯服務卡住時，40 秒的翻譯預算只夠等兩三個請求，一輪就跑不完了
HTTP_TIMEOUT = 8
# 這一輪超過這個比例的請求失敗，就當作整輪失敗（網路斷了、整個被擋），不寫檔
FAIL_RATIO = 0.8


# ---------------------------------------------------------------- 抓取

def _targets(cfg):
    """要抓新聞的每一檔。棕櫚油不在 watchlist.json，是 fetch.py 另外加的（MPOB 牌價）。"""
    out = []
    for s in cfg["stocks"]:
        sym = s["symbol"]
        out.append({
            "symbol": sym,
            "query": s.get("q") or re.sub(r"[^\w\s&().-]", " ", s.get("name", "")).strip(),
            "aliases": [a for a in s.get("aliases") or [] if a],
            "zh": [z for z in s.get("zh") or [] if z],
            "sa": s.get("sa") if s.get("sa") and ":" not in s["sa"] else None,
            # klsescreener 的代碼保留字尾：KLCC 是 5235SS，拿掉 SS 會 404
            "code": sym[:-3] if sym.endswith(".KL") else None,
            "is_index": bool(s.get("is_index")),
            "sector": s.get("sector") or "",
        })
    # 查詢詞不用 fetch.py 的 "crude palm oil Malaysia"：Google 對整句做精確比對，
    # 那一句近 30 天找不到任何報導（data.js 的棕櫚油新聞一直是空的）
    out.append({"symbol": "MPOB-CPO", "query": "crude palm oil", "aliases": [],
                "zh": [], "sa": None, "code": None, "is_index": True, "sector": "油棕"})
    return out


def _klci_time():
    """綜指最後成交時間（Yahoo）。用來判斷漲跌排行是「今天盤中」還是「上一個交易日」。

    只看時鐘的話公共假期會算錯（假日照樣寫成今天），所以先問 Yahoo。
    """
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EKLSE?range=1d&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": news.UA})
    with urllib.request.urlopen(req, timeout=15, context=ssl.create_default_context()) as r:
        meta = json.loads(r.read().decode("utf-8"))["chart"]["result"][0]["meta"]
    return datetime.fromtimestamp(meta["regularMarketTime"], MYT)


def _safe(fn, *args, **kw):
    """任何一個來源掛掉都只是少那一塊，不能讓整輪更新失敗。
    失敗要回報出來（第二個值）：呼叫端靠它決定哪一塊沿用上一輪、整輪算不算失敗。"""
    try:
        return fn(*args, **kw), None
    except Exception as exc:  # noqa: BLE001
        return None, "{}: {}".format(getattr(fn, "__name__", fn), exc)


def fetch_all(targets, workers=8):
    """所有網路請求一起丟進執行緒池。

    這些函式（google_news、klse_news、bursa_announcements、market.quotes）只用區域變數，
    同時跑是安全的。會共用的快取（翻譯、網址）在 main() 開頭就先載入，
    翻譯則全部回到主執行緒做。

    news.py 的抓取函式預設「抓不到就回空 list」（fetch.py 靠這個不中斷），
    這裡一律傳 strict=True：抓不到要丟例外，才分得出「沒新聞」和「被擋了」。
    回傳 (結果, [(工作名稱, 錯誤訊息)], 工作數)；失敗的工作結果是 None。
    """
    jobs = {}
    with ThreadPoolExecutor(workers) as ex:
        # 最大的那頁（全市場報價，約 3MB）先丟，免得它最後一個才開始
        jobs["quotes"] = ex.submit(_safe, market.quotes)
        jobs["klci"] = ex.submit(_safe, _klci_time)
        jobs["heads"] = ex.submit(_safe, news.market_headlines, limit=HEADLINES, strict=True)
        jobs["latest"] = ex.submit(_safe, news.klse_news, strict=True)
        for t in targets:
            sym = t["symbol"]
            if t["query"]:
                jobs["g|" + sym] = ex.submit(_safe, news.google_news, t["query"], limit=ARTICLES,
                                             aliases=t["aliases"], max_window="7d", strict=True)
            if t["code"]:
                jobs["k|" + sym] = ex.submit(_safe, news.klse_news, t["code"], strict=True)
                jobs["a|" + sym] = ex.submit(_safe, news.bursa_announcements, t["code"],
                                             limit=FILINGS, ajax=True, strict=True)
        results, errors = {}, []
        for key, fut in jobs.items():
            val, err = fut.result()
            results[key] = val
            if err:
                errors.append((key, err))
    return results, errors, len(jobs)


def print_errors(errors):
    """失敗的工作依「哪一類、什麼錯」合併成一行：klsescreener 整個被擋時是 55 個一樣的 403，
    一行一個會把紀錄洗掉，只看得到最後幾行。"""
    groups = {}
    for key, msg in errors:
        kind, _, sym = key.partition("|")
        groups.setdefault((kind, msg), []).append(sym)
    names = {"g": "Google 新聞", "k": "klsescreener 新聞", "a": "公告"}
    for (kind, msg), syms in groups.items():
        syms = [x for x in syms if x]
        who = "{} {} 檔（{}{}）".format(names.get(kind, kind), len(syms), "、".join(syms[:4]),
                                     "⋯" if len(syms) > 4 else "") if syms else kind
        print("  ✗ {}：{}".format(who, msg), flush=True)


# ---------------------------------------------------------------- 整理

def _mentions(text, t):
    """這段文字有沒有提到這家公司（英文名、Bursa 簡稱、中文報的稱呼）。

    英文名不能用 \\b：中文字在 Python 裡也算「字」，
    「马来亚银行（MAYBANK,1155」的 行 和 M 之間不算邊界。
    """
    low = text.lower()
    if any(re.search(re.escape(z.lower()) + BROKER_AFTER, low) for z in t["zh"]):
        return True
    for n in [t["query"], t["sa"]] + t["aliases"]:
        if n and n.lower() not in WEAK_NAMES and re.search(
                r"(?<![a-z0-9])" + re.escape(n.lower()) + r"(?![a-z0-9])" + BROKER_AFTER, low):
            return True
    return False


def _coded(text, t):
    """中文報的寫法「（GAMUDA,5398,主板建筑组）」「（MAYBANK，1155，主要板金融）」。
    代碼一定要夾在括號與逗號之間才算——MRCB 的代碼 1651 跟綜指點數 1651.17 撞在一起。"""
    return bool(t["code"] and re.search(
        r"[,，(（]\s*" + re.escape(t["code"]) + r"\s*[,，)）]", text, re.I))


def _dup(a, b):
    if a["url"] == b["url"]:
        return True
    ca, cb = CJK.search(a["title"]), CJK.search(b["title"])
    if ca and cb:
        return news._same_event_zh(a["title"], b["title"])
    if not ca and not cb:
        return news._same_event(a["title"], b["title"])
    return False       # 一中一英比不出來，兩則都留


def _is_wrap(a):
    return bool(MARKET_ZH.search(a["title"]) or MARKET_EN.search(a["title"]))


def _merge(groups, limit):
    """依序加入、同一事件只留先加入的那則，最後依時間新到舊。"""
    pool = []
    for items in groups:
        for a in items:
            if news._is_noise(a) or any(_dup(a, b) for b in pool):
                continue
            pool.append(a)
    pool.sort(key=lambda a: -(a.get("ts") or 0))
    return pool[:limit]


def stock_articles(t, gitems, kitems, wraps):
    """一檔股票的媒體報導：klsescreener（依代碼標好、有中文報）＋ Google News（1 天、7 天）。

    klsescreener 的排前面：同一件事它有原文網址和精確時間，Google 的是轉址。
    """
    sector = SECTOR_WORDS.get(t["sector"])
    mine = []
    for a in kitems or []:
        if news._ROUNDUP.search(a["title"]) or len(ROUNDUP_SEP.findall(a["title"])) >= 3:
            continue                        # 多家公司的綜合報導，對單一公司資訊量很低
        if _mentions(a["title"], t):
            mine.append(a)
        elif _is_wrap(a):
            continue                        # 大盤報導放「市場快訊」
        elif a["tags"] > 8:
            continue                        # 標了十幾檔的綜合報導（Brokers Digest 之類）
        elif (_mentions(a.get("summary") or "", t) or _coded(a.get("summary") or "", t)
              or (sector and sector.search(a["title"]))):
            mine.append(a)

    # 指數、匯率、棕櫚油沒有「公司名」可比對，Google 找到的都留；
    # 個股只留真的在講這家公司的（_tier < 2），寬鬆比對到的無關報導不要
    names = [n for n in [t["query"]] + t["aliases"] if n.lower() not in WEAK_NAMES]
    goog = [a for a in gitems or [] if not PRICE_PAGE.search(a["title"])
            and (t["is_index"] or news._tier(a, names) < 2)]

    if t["symbol"] == "^KLSE":
        return _merge([wraps, goog], ARTICLES)
    return _merge([mine, goog], ARTICLES)


def movers_board(qs, mt, now):
    """漲跌排行，欄位跟 fetch.py 寫進 data.js 的 market.movers 一模一樣。"""
    mv = market.movers(qs)
    keys = ("short", "name", "code", "price", "pct", "mcap", "sector_zh", "board")
    out = {"up": [{k: q[k] for k in keys} for q in mv["up"]],
           "down": [{k: q[k] for k in keys} for q in mv["down"]],
           "universe": mv["universe"], "total": mv["total"]}
    out["as_of"] = mt.strftime("%Y-%m-%d")
    # live 一定要寫，收盤後寫 false：網頁是把這份疊在 data.js 的 movers 上（{...舊, ...新}），
    # 少了這個欄位，data.js 下午那份的 live:true 和時間就會留著，
    # 收盤排行被標成「今天盤中 15:42」。time 只在盤中才有。
    # Bursa 下午 5 點收盤；收盤後 Yahoo 的時間戳停在 5 點左右（跟 fetch.py 同樣的判斷）
    out["live"] = False
    if mt.date() == now.date() and (now.hour, now.minute) < (17, 10):
        out["live"] = True
        out["time"] = now.strftime("%H:%M")
    return out


def _last_session(now):
    """Yahoo 掛掉時的備案：只看時鐘推最近一個交易日（公共假期會算錯，但總比不寫好）。"""
    d = now.date()
    if now.weekday() < 5 and (now.hour, now.minute) >= (9, 0):
        return now if (now.hour, now.minute) < (17, 0) else now.replace(hour=17, minute=0, second=0)
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return datetime(d.year, d.month, d.day, 17, 0, tzinfo=MYT)


def quote_map(qs, targets, ts):
    """追蹤清單裡的馬股現價。漲跌幅自己用漲跌金額算到小數兩位——
    klsescreener 的百分比只到一位，跟 data.js 的兩位並排會對不起來。"""
    by_code = {q["code"]: q for q in qs}
    out = {}
    for t in targets:
        q = by_code.get(t["code"]) if t["code"] else None
        if not q or not q.get("price"):
            continue
        pct = q.get("pct")
        prev = q["price"] - (q.get("change") or 0)
        if q.get("change") is not None and prev > 0:
            pct = round(q["change"] / prev * 100, 2)
        out[t["symbol"]] = {"price": q["price"], "change_pct_1d": pct, "ts": ts}
    return out


# ---------------------------------------------------------------- 上一輪（當快取）

def read_prev(path):
    """讀上一輪的 news.json。檔案不存在、是空的（news 分支還沒建立時 git show 會留下空檔）
    或壞掉，都當作沒有（回傳 {}）。"""
    try:
        with open(path, encoding="utf-8") as fh:
            prev = json.load(fh)
        return prev if isinstance(prev, dict) else {}
    except Exception:
        return {}


def load_prev(prev):
    """上一輪的 news.json 當快取。回傳 (標題→譯文, 網址→原文網址, 網址→公告內文, 標題→夾英文的譯文)。"""
    titles, sources, bodies, weak = {}, {}, {}, {}

    def learn(a):
        t, zh = a.get("title"), a.get("title_zh")
        # 沒翻成功（譯文 = 原文：英文沒翻、簡體沒轉）的不沿用，這一輪再試一次。
        # 少數簡體標題本來就沒有要轉的字，每輪會重送一次，但它們跟著批次走，幾乎不花時間。
        # 英文標題的半成品也不沿用（「新聞稿 Sentral REIT 1H 2026 Realised Net Income⋯」，
        # 翻譯失敗時 filing_title 只加了前綴）：它有中文字，網頁不會補翻，
        # 沿用的話會一直掛到這則公告被擠出清單為止，對 REIT 可能是好幾個月。
        # 規則表翻的公告（人名留英文）也會被挑掉，但規則表不用網路，重翻不花時間
        if t and zh and zh != t and (CJK.search(t) or not translate.half_done(t, zh)):
            titles[t] = zh
        elif t and zh and zh != t:
            # 夾英文的譯文不當快取（這一輪會重翻），但翻譯服務整個掛掉時，
            # 它還是比純英文好——留著當最後的備用
            weak[t] = zh
        if a.get("source_url"):
            sources[a["url"]] = a["source_url"]

    for a in prev.get("headlines") or []:
        learn(a)
    for s in (prev.get("stocks") or {}).values():
        for a in s.get("articles") or []:
            learn(a)
        for f in s.get("filings") or []:
            learn(f)
            if f.get("body_zh"):
                bodies[f["url"]] = {k: f[k] for k in ("body_zh", "move") if k in f}
    return titles, sources, bodies, weak


# ---------------------------------------------------------------- 翻譯

RULES = [re.compile(p, re.I) for p, _ in translate.FILING_RULES]
NEWS_RELEASE = "News Release"


def _rule_title(title):
    """公告標題規則表翻得出來（不用網路）。"""
    return any(r.match(title) for r in RULES)


def translate_all(headlines, stocks, prev_titles, budget, prev_weak=None):
    """標題全部轉成繁體中文。只在主執行緒做（translate 的快取與計數不是執行緒安全的）。

    - 中文報（簡體）→ 一次批次轉繁體，幾百則也只要幾個請求
    - 英文 → translate.text_zh（先查 main 的翻譯快取），公告標題先走規則表
    - 超過時間預算就不再打翻譯 API，只用快取；沒翻到的留英文，網頁打開時會現翻，
      下一輪再補。每 5 分鐘一輪，寧可少翻幾則，也不能拖到下一輪都還沒跑完
    所以順序很重要：市場快訊 → 馬股（使用者真正持有的）→ 公告 → 美股與各國指數。

    預算從第一個批次請求就開始算（translate.DEADLINE）：翻譯服務卡住時，
    光是批次就能等掉一分多鐘，逐句翻的預算還沒開始算，一輪就超過 90 秒。
    """
    translate.DEADLINE = time.time() + budget
    mine = [sym for sym in stocks if sym.endswith(".KL") or sym in ("^KLSE", "MPOB-CPO")]
    rest = [sym for sym in stocks if sym not in mine]
    first = list(headlines) + [a for sym in mine for a in stocks[sym]["articles"]]
    filings = [f for s in stocks.values() for f in s["filings"]]
    later = [a for sym in rest for a in stocks[sym]["articles"]]

    zh_items = [a for a in first + filings + later if CJK.search(a["title"])]
    todo = [a["title"] for a in zh_items if a["title"] not in prev_titles]
    # keep_failed=False：沒轉成的拿到 None。不能把簡體原文當成 title_zh 發出去——
    # 網頁看 title_zh 有中文字就不會再處理，使用者就一直看到簡體。
    # 沒轉成的不寫 title_zh、標 lang: zh（見 _slim），網頁會自己轉繁體
    conv = dict(zip(todo, translate.trad_many(todo, keep_failed=False)))
    for a in zh_items:
        zh = prev_titles.get(a["title"]) or conv.get(a["title"])
        if zh:
            a["title_zh"] = zh
        else:
            a.pop("title_zh", None)     # 沿用上一輪的條目可能帶著舊的值

    # 英文先整批翻一次（幾個請求就翻完上百則），結果進 translate 的快取；
    # 下面逐句的 text_zh／filing_title 會先查快取，只有批次沒過驗收的才逐句重翻。
    # 公告標題大多是套版的，規則表就翻得出來，不必送。
    # 「News Release ⋯」要拿掉前綴再送：filing_title 查快取用的是拿掉前綴的那句，
    # 整句送的話批次翻得再好也對不上，這類公告永遠只能逐句翻
    batch = [a["title"] for a in first + later
             if not CJK.search(a["title"]) and a["title"] not in prev_titles]
    batch += [f["title"][len(NEWS_RELEASE):].strip() if f["title"].startswith(NEWS_RELEASE)
              else f["title"] for f in filings
              if not CJK.search(f["title"]) and f["title"] not in prev_titles
              and not _rule_title(f["title"])]
    ok0 = translate.STATS.get("Google 批次", 0)
    bad0 = translate.STATS.get("Google 批次 失敗", 0)
    translate.text_zh_many(batch)
    # 整批一個都沒成功（被擋、限流、逾時）：逐句翻用的是同一個服務，
    # 多半也一樣失敗，每句還要停 0.35 秒——直接改成只查快取，剩下的網頁打開時會現翻
    if translate.STATS.get("Google 批次", 0) == ok0 and translate.STATS.get("Google 批次 失敗", 0) > bad0:
        translate.MAX_CALLS = 0

    def english(a, fn):
        if CJK.search(a["title"]):
            return
        if a["title"] in prev_titles:
            a["title_zh"] = prev_titles[a["title"]]
            return
        # 時間到了（translate.DEADLINE）、或每個引擎都連續失敗到被跳過，
        # text_zh 會自己直接回傳原文、不打 API；公告的規則表不用網路，照翻
        zh = fn(a["title"])
        if zh == a["title"] and prev_weak and a["title"] in prev_weak:
            zh = prev_weak[a["title"]]          # 這一輪沒翻成：用上一輪夾英文的版本
        a["title_zh"] = zh

    for a in first:
        english(a, translate.text_zh)
    for f in filings:
        english(f, translate.filing_title)      # 規則表不用網路，超過預算也照翻
        f["source_zh"] = translate.category(f["source"])
    for a in later:
        english(a, translate.text_zh)


def filing_bodies(stocks, prev_bodies, workers=8):
    """持股變動公告的內容（誰、增持或減持多少股）。

    規則解析、不用翻譯，但每則要多抓一頁：上一輪有的直接沿用，
    新的每輪最多 BODIES 則（最新的優先），剩下的下一輪再補。
    """
    need = []
    for s in stocks.values():
        for i, f in enumerate(s["filings"]):
            old = prev_bodies.get(f["url"])
            if old:
                f.update(old)
            # 類別是最準的判斷；譯文判斷是 fetch.py 的做法，
            # 但「Changes in Director's Interest (Section 219)」會被機器翻成「董事權益變更」而漏掉
            elif i < 8 and (f["source"] == "Changes in Shareholdings"
                            or any(k in (f.get("title_zh") or "") for k in HOLDING)):
                need.append(f)
    need.sort(key=lambda f: -(f.get("ts") or 0))
    need = need[:BODIES]
    if not need:
        return 0
    with ThreadPoolExecutor(workers) as ex:
        got = list(ex.map(lambda f: _safe(news.announcement_body, f["url"])[0] or ("", ""), need))
    n = 0
    for f, (_body_en, info) in zip(need, got):
        if isinstance(info, dict):
            f["body_zh"] = info["text"]
            # key 用來去重：同一筆成交會被第138條與第219條各公告一次（網頁合併用）
            f["move"] = {k: info[k] for k in ("key", "holder", "act", "shares", "pct")}
            n += 1
    return n


# ---------------------------------------------------------------- 輸出

ART_KEYS = ("title", "title_zh", "url", "source", "ts", "date", "source_url")
FIL_KEYS = ("title", "title_zh", "url", "source", "source_zh", "ts", "date", "body_zh", "move")


def _slim(a, keys):
    """只留網頁用得到的欄位。when（「3 小時前」）是抓取當下算的，過一陣子就不準，
    網頁一律用 ts／date 自己算，所以不放。

    標題有中文字的（大馬中文報，原文是簡體）一律加 lang: "zh"：
      - 網頁靠它把這則當中文報處理：點標題在 App 裡讀，不顯示「原文」切換
      - 簡體沒轉成繁體時不寫 title_zh（不把簡體冒充成繁體譯文），
        網頁看到 lang: zh 又沒有 title_zh，會自己批次轉繁體
    英文沒翻成的照舊 title_zh = 原文，網頁看沒有中文字會現翻。
    """
    out = {k: a[k] for k in keys if a.get(k) not in (None, "", 0)}
    if "ts" in out:
        out["ts"] = int(out["ts"])
    if CJK.search(a["title"]):
        out["lang"] = "zh"
    else:
        out.setdefault("title_zh", a["title"])
    return out


def _untranslated(a):
    """英文標題的譯文狀態：0 = 翻好了，1 = 夾雜較多英文（多半是公司名、人名，
    也可能只翻了一半），2 = 沒翻（除了「新聞稿」前綴沒有中文）。
    規則表翻的公告不算：人名本來就留英文。"""
    if CJK.search(a["title"]) or _rule_title(a["title"]):
        return 0
    if not translate.half_done(a["title"], a.get("title_zh")):
        return 0
    zh = a.get("title_zh") or ""
    if zh.startswith("新聞稿"):
        zh = zh[len("新聞稿"):]
    return 1 if CJK.search(zh) else 2


def _fresh_movers(pm, now):
    """上一輪的漲跌排行能不能沿用。盤中那份只能在同一天用：
    隔天還寫著 live:true，網頁會標成「今天盤中 11:00」，其實是昨天的。"""
    if not pm or not isinstance(pm.get("up"), list) or not isinstance(pm.get("down"), list):
        return None
    if pm.get("live") and pm.get("as_of") != now.strftime("%Y-%m-%d"):
        return None
    pm = dict(pm)
    pm["live"] = bool(pm.get("live"))       # 舊版 news.json 沒有 live 欄位
    if not pm["live"]:
        pm.pop("time", None)
    return pm


def _gh_warning(msg):
    """在 GitHub Actions 的執行摘要上掛一個黃色警告。工作照樣是綠的（整輪失敗也不讓它變紅：
    每 5 分鐘一封失敗通知信太吵），但打開 Actions 頁面一眼就看得到，不會默默壞好幾天。"""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("::warning title=即時新聞::" + msg, flush=True)


def main():
    ap = argparse.ArgumentParser(description="快速新聞更新，輸出 news.json")
    ap.add_argument("--out", default="_news", help="輸出資料夾（news.json 寫在裡面）")
    ap.add_argument("--prev", help="上一輪的 news.json（預設是 --out 裡現有的那份）")
    ap.add_argument("--budget", type=float, default=40,
                    help="機器翻譯最多花幾秒（之後只用快取）")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    t0 = time.time()
    with open(os.path.join(HERE, "watchlist.json"), encoding="utf-8") as fh:
        targets = _targets(json.load(fh))

    out_path = os.path.join(args.out, "news.json")
    prev = read_prev(args.prev or out_path)
    prev_titles, prev_sources, prev_bodies, prev_weak = load_prev(prev)
    prev_stocks = prev.get("stocks") or {}

    # 共用的快取先載入，再開執行緒：兩個模組的快取都是「第一次用到才讀檔」，
    # 多個執行緒同時第一次用到會各讀一份、互相蓋掉
    translate._load_cache()
    translate.HTTP_TIMEOUT = HTTP_TIMEOUT
    url_cache = news._load_url_cache()

    res, errors, n_req = fetch_all(targets, args.workers)
    failed = {key for key, _ in errors}
    t_fetch = time.time() - t0

    # ---- 市場快訊：Google News（英文）＋ 中文報的大盤報導
    # 大盤報導來自各檔的 klsescreener 新聞（會標上當天提到的股票）和全站最新 20 則。
    # 個股那幾份排前面：同一篇在全站清單裡沒有原文網址，只連得到 klsescreener 的轉載頁
    cutoff = time.time() - 3 * 86400
    wraps, seen = [], set()
    for key in [k for k in res if k.startswith("k|")] + ["latest"]:
        for a in res.get(key) or []:
            if _is_wrap(a) and a["ts"] >= cutoff and a["url"] not in seen:
                seen.add(a["url"])
                wraps.append(a)
    wraps.sort(key=lambda a: -a["ts"])
    # klsescreener 的排在 Google 前面：同一篇兩邊都有時，留原文網址、時間精確的那則
    headlines = _merge([wraps, res.get("heads") or []], HEADLINES)

    # ---- 個股（這一輪真的抓到的）
    fresh = {}
    for t in targets:
        sym = t["symbol"]
        arts = stock_articles(t, res.get("g|" + sym), res.get("k|" + sym), wraps)
        # 清單是依日期排的，同一天之內不一定依時間；網頁要新到舊
        fils = sorted(res.get("a|" + sym) or [], key=lambda f: -(f.get("ts") or 0))
        fresh[sym] = (arts, fils)
    qs = res.get("quotes")
    n_fresh = len(headlines) + sum(len(a) + len(f) for a, f in fresh.values())

    # ---- 整輪失敗：不寫檔
    # 網路斷了、整個被擋時，寫出去的會是一份幾乎全空、時間卻是最新的檔案：
    # 網頁會顯示「新聞 HH:MM 更新」，上一輪的快訊、報價、漲跌排行卻不見了，
    # 翻譯快取（就是上一輪的 news.json）也跟著清空。所以乾脆不寫，上一輪的原封不動留著，
    # workflow 看到檔案沒變就不推
    if (not n_fresh and not qs) or len(errors) > FAIL_RATIO * n_req:
        print("✗ 這一輪 {}/{} 個來源失敗（抓到快訊 {}、個股新聞與公告 {}、報價 {}）："
              "不寫 news.json，保留上一輪的（{}）".format(
                  len(errors), n_req, len(headlines), n_fresh - len(headlines), len(qs or []),
                  prev.get("generated_at") or "沒有上一輪"), flush=True)
        print("耗時 {:.1f} 秒".format(time.time() - t0), flush=True)
        print_errors(errors)
        _gh_warning("{}/{} 個來源失敗，這一輪沒有更新 news.json".format(len(errors), n_req))
        return

    # ---- 抓不到的那幾塊沿用上一輪
    # 每一塊只在「這一輪抓不到」時才沿用，抓到了就以這一輪為準（包括真的沒有新聞）
    carried, warn = [], []
    if "heads" in failed or "latest" in failed:
        old = [a for a in prev.get("headlines") or [] if (a.get("ts") or 0) >= cutoff]
        if old:
            headlines = _merge([headlines, old], HEADLINES)
            carried.append("快訊")

    month = time.time() - 30 * 86400
    stocks, n_old_art, n_old_fil, silent = {}, 0, 0, []
    for t in targets:
        sym = t["symbol"]
        arts, fils = fresh[sym]
        old = prev_stocks.get(sym) or {}
        if (("g|" + sym) in failed or ("k|" + sym) in failed
                or (sym == "^KLSE" and "latest" in failed)) and old.get("articles"):
            keep = [a for a in old["articles"] if (a.get("ts") or 0) >= month]
            if keep:
                arts = _merge([arts, keep], ARTICLES)
                n_old_art += 1
        # 公告清單是歷史紀錄，不會憑空變成 0 則：抓不到、或抓到 0 則但上一輪有，都沿用上一輪。
        # 後者沒有錯誤訊息（多半是網頁改版、解析不到），另外記一筆 ✗，不然不會有人發現
        if (("a|" + sym) in failed or (t["code"] and not fils)) and old.get("filings"):
            fils = list(old["filings"])
            n_old_fil += 1
            if ("a|" + sym) not in failed:
                silent.append(sym)
        if arts or fils:
            stocks[sym] = {"articles": arts, "filings": fils}
    if n_old_art:
        carried.append("{} 檔新聞".format(n_old_art))
    if n_old_fil:
        carried.append("{} 檔公告".format(n_old_fil))
    if silent:
        warn.append("公告：{} 檔沒有錯誤卻抓到 0 則（上一輪有），沿用上一輪（{}{}）".format(
            len(silent), "、".join(silent[:4]), "⋯" if len(silent) > 4 else ""))

    # ---- 漲跌排行與現價
    now = datetime.now(MYT)
    mt = res.get("klci") or _last_session(now)
    movers, quotes = None, {}
    if qs:
        movers = movers_board(qs, mt, now)
        quotes = quote_map(qs, targets, int(now.timestamp() if movers["live"] else mt.timestamp()))
    else:
        if qs is not None:
            warn.append("報價：全市場報價抓到 0 筆")
        # 報價抓不到：沿用上一輪的報價和漲跌排行。報價帶著自己的 ts，
        # 網頁會跟 data.js 比，比較舊的不會蓋上去
        movers = _fresh_movers(prev.get("movers"), now)
        quotes = prev.get("quotes") or {}
        if movers or quotes:
            carried.append("報價與漲跌排行")

    # ---- 翻譯與公告內文（主執行緒）
    t1 = time.time()
    translate_all(headlines, stocks, prev_titles, args.budget, prev_weak)
    n_body = filing_bodies(stocks, prev_bodies, args.workers)
    t_tr = time.time() - t1

    # Google 的轉址網址：3 小時那支解析過的（data/url_cache.json）或上一輪留下的，直接沿用
    for a in headlines + [x for s in stocks.values() for x in s["articles"]]:
        real = prev_sources.get(a["url"]) or url_cache.get(a["url"])
        if real and real != a["url"]:
            a["source_url"] = real

    now = datetime.now(MYT)
    payload = {
        "v": 1,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S (MYT)"),
        "ts": int(now.timestamp()),
        "headlines": [_slim(a, ART_KEYS) for a in headlines],
        "stocks": {sym: {"articles": [_slim(a, ART_KEYS) for a in s["articles"]],
                         "filings": [_slim(f, FIL_KEYS) for f in s["filings"]]}
                   for sym, s in stocks.items()},
        "quotes": quotes,
    }
    if movers:
        payload["movers"] = movers

    os.makedirs(args.out, exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        # 不縮排：手機每 3 分鐘讀一次，檔案越小越好
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, out_path)

    items = payload["headlines"] + [x for s in payload["stocks"].values()
                                    for x in s["articles"] + s["filings"]]
    n_art = sum(len(s["articles"]) for s in payload["stocks"].values())
    n_fil = sum(len(s["filings"]) for s in payload["stocks"].values())
    state = [_untranslated(a) for a in items]
    left_cn = sum(1 for a in items if a.get("lang") == "zh" and "title_zh" not in a)
    print("news.json：快訊 {}、{} 檔（新聞 {}、公告 {}，新解析持股變動 {}）、漲跌 {}/{}、報價 {}；"
          "未翻譯 {} 則（另 {} 則夾雜較多英文）、簡體未轉 {} 則；{:.0f} KB".format(
              len(payload["headlines"]), len(payload["stocks"]), n_art, n_fil, n_body,
              len(movers["up"]) if movers else 0, len(movers["down"]) if movers else 0,
              len(quotes), state.count(2), state.count(1), left_cn,
              os.path.getsize(out_path) / 1024), flush=True)
    if carried:
        print("  ↺ 這一輪抓不到、沿用上一輪：" + "、".join(carried), flush=True)
    print("耗時 {:.1f} 秒（抓取 {:.1f} 秒／{} 個工作、翻譯與內文 {:.1f} 秒）".format(
        time.time() - t0, t_fetch, n_req, t_tr), flush=True)
    print("翻譯引擎：{}".format(translate.STATS or "全部命中快取"), flush=True)
    print_errors(errors)
    for w in warn:
        print("  ✗ " + w, flush=True)
    if errors or warn:
        _gh_warning("{}/{} 個來源失敗{}".format(
            len(errors), n_req, "，沿用上一輪：" + "、".join(carried) if carried else ""))


if __name__ == "__main__":
    main()
