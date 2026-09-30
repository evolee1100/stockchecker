#!/usr/bin/env python3
"""把英文的新聞標題與 Bursa 公告轉成繁體中文。

三層策略，由準到糙：
  1. 規則表   —— Bursa 公告標題是套版的，用規則翻比機器翻譯準，而且不會失敗
  2. 機器翻譯 —— 新聞標題是自由文字，只能靠 API（Google → MyMemory 兩層備援）
  3. 原文     —— 全部失敗就保留英文，絕不輸出半截或亂碼

翻譯結果快取在 data/translations.json 並進版控，所以每天只需要翻新增的標題，
第一天之後 API 用量很小。公司名等專有名詞會先換成佔位符保護，
還原失敗就整句退回英文。
"""

import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "data", "translations.json")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# ---------------------------------------------------------------- 專有名詞
# 這些不能翻（翻了會變成「中環房地產投資信託」這種東西）
TERMS = [
    "Sentral REIT", "IGB REIT", "Pavilion REIT", "Axis REIT", "KLCCP Stapled",
    "KLCC REIT", "Al-`Aqar", "Maybank", "CIMB Group", "CIMB", "Public Bank",
    "Hong Leong Financial Group", "Hong Leong Bank", "RHB Bank", "RHB",
    "AmBank", "AMMB", "Alliance Bank", "Bank Islam", "Affin Bank", "AFFIN",
    "MBSB", "MRCB", "Bursa Malaysia", "Bank Negara Malaysia", "Bank Negara",
    "FBM KLCI", "KLCI", "Menara Shell", "Platinum Sentral", "WORQ",
    "The Edge", "EdgeProp", "Bernama",
    # 金融縮寫：翻成中文反而會錯（OPR 曾被翻成「營運報酬率」，實為隔夜政策利率），
    # 而且馬股投資人本來就看英文縮寫
    "OPR", "DPU", "NPI", "EPS", "ROE", "ROA", "NIM", "GIL", "CASA", "NAV",
    "MPC", "BNM", "EPF", "KWAP", "PNB", "IPO", "AGM", "EGM", "MSCI", "FTSE",
    "ESG", "REIT", "REITs", "M-REIT", "M-REITs", "GDP", "CPI", "RM",
]

# ---------------------------------------------------------------- 金融慣用語
# 機器翻譯對這些片語會字面直譯：bargain hunting →「特價狩獵」、
# at opening →「在打開便宜貨時」、what to expect →「好玩新鮮事」。
# 送翻譯之前先換成正確的中文，Google 遇到中文會原樣保留。
# 順序很重要：長片語要排在它包含的短片語前面。
PHRASES = [
    (r"what to expect on (Bursa Malaysia|Bursa|the market)", "\\1 盤前展望："),
    (r"bargain[- ]hunting (emerges|returns|kicks in)", "逢低買盤進場"),
    (r"bargain[- ]hunting", "逢低買盤"),
    (r"profit[- ]taking", "獲利了結"),
    (r"window[- ]dressing", "作帳行情"),
    # foreign selling、selling pressure 不再事先替換：佔位符把句子切斷後，
    # 引擎把「as foreign selling pressure persists」譯成「外資賣壓 仍堅挺」（意思相反）。
    # 讓引擎整句翻（「由於外國拋售壓力持續」），再由 POST 把用詞改成習慣說法。
    (r"foreign buying", "外資買盤"),
    (r"foreign (funds|investors) (net )?(sold|sell|dumped)", "外資賣超"),
    (r"foreign (funds|investors) (net )?(bought|buy)", "外資買超"),
    (r"net (sellers|selling)", "賣超"),
    (r"net (buyers|buying)", "買超"),
    (r"at (the )?open(ing)?( bell)?", "開盤時"),
    (r"at (the )?(mid-?day|midday) break", "午盤時"),
    (r"at (the )?close(?! to)", "收盤時"),
    # 修飾詞要逐一列出對照。用 \1 帶入的話 "sharply" 會原樣留在中文裡
    (r"opens? sharply higher", "大幅開高"),
    (r"opens? sharply lower", "大幅開低"),
    (r"opens? slightly higher", "小幅開高"),
    (r"opens? slightly lower", "小幅開低"),
    (r"(ends?|closes?|finish(es)?) sharply higher", "大幅收高"),
    (r"(ends?|closes?|finish(es)?) sharply lower", "大幅收低"),
    (r"(ends?|closes?|finish(es)?) slightly higher", "小幅收高"),
    (r"(ends?|closes?|finish(es)?) slightly lower", "小幅收低"),
    (r"opens? higher", "開高"),
    (r"opens? lower", "開低"),
    (r"(ends?|closes?|finish(es)?) higher", "收高"),
    (r"(ends?|closes?|finish(es)?) lower", "收低"),
    # 「retreats sharply」只換 sharply 會變成「回落 大幅」，動詞一起換
    (r"(retreats?|retreated|pulls? back) sharply", "大幅回落"),
    (r"(falls?|fell|drops?|dropped|slides?|slid|slumps?|slumped) sharply", "大幅下跌"),
    (r"(rises?|rose|climbs?|climbed|gains?|gained|jumps?|jumped) sharply", "大幅上漲"),
    (r"(rebounds?|rebounded) sharply", "大幅反彈"),
    (r"sharply", "大幅"),
    (r"caution lingers", "謹慎情緒未退"),
    (r"cautious sentiment", "謹慎情緒"),
    (r"risk-off", "避險情緒"),
    (r"risk-on", "風險偏好回升"),
    (r"blue[- ]chips?", "藍籌股"),
    (r"key index", "綜合指數"),
    (r"benchmark index", "基準指數"),
    (r"(rising|higher) (bond )?yields", "殖利率上升"),
    (r"rate cut", "降息"),
    (r"rate hike", "升息"),
    (r"private placement", "私募配售"),
    (r"rights issue", "附加股發行"),
    (r"bonus issue", "紅股發行"),
    (r"final (single[- ]tier )?dividend", "末期股息"),
    (r"(first |second )?interim (single[- ]tier )?dividend", "中期股息"),
    (r"special (single[- ]tier )?dividend", "特別股息"),
    (r"single[- ]tier dividend", "單層股息"),
    (r"dividend", "股息"),
    (r"target price", "目標價"),
    (r"upgrade[sd]?", "調升評級"),
    (r"downgrade[sd]?", "調降評級"),
]
PHRASES = [(re.compile(r"\b" + p + r"\b", re.I), zh) for p, zh in PHRASES]


def _phrases(text):
    for pat, zh in PHRASES:
        text = pat.sub(lambda m: m.expand(zh), text)
    return text


# ---------------------------------------------------------------- 公告類別
CATEGORY = {
    "Listing Circulars": "上市通函",
    "Entitlements": "權益事項",
    "Financial Results": "財務業績",
    "General Announcement": "一般公告",
    "Changes in Shareholdings": "持股變動",
    "Changes in Boardroom": "董事會異動",
    "Annual Report": "年報",
    "Circular / Notice to Shareholders": "致股東通函",
    "Bursa 公告": "Bursa 公告",
}

# ---------------------------------------------------------------- 機構名稱
ORGS = {
    "EMPLOYEES PROVIDENT FUND BOARD": "僱員公積金局 (EPF)",
    "KUMPULAN WANG PERSARAAN (DIPERBADANKAN)": "退休基金局 (KWAP)",
    "AMANAHRAYA TRUSTEES BERHAD": "信託局 (AmanahRaya)",
    "PERMODALAN NASIONAL BERHAD": "國民投資公司 (PNB)",
    "LEMBAGA TABUNG HAJI": "朝聖基金局",
}

# ---------------------------------------------------------------- 公告標題規則
# (正規表示式, 取代樣板) —— 由specific到general，先命中的贏
FILING_RULES = [
    (r"^Quarterly rpt on consolidated results for the financial period ended (\d{2})/(\d{2})/(\d{4})$",
     lambda m: "季度綜合業績報告（財務期間至 {}-{}-{}）".format(m.group(3), m.group(2), m.group(1))),
    (r"^Changes in Sub\.? S-hldr'?s Int\.? \(Section 138 of CA 2016\)\s*[-–]\s*(.+)$",
     lambda m: "主要股東持股變動（2016年公司法第138條）— " + _org(m.group(1))),
    (r"^Changes in Sub\.? S-hldr'?s Int\.? \(Section 138 of CA 2016\)$",
     lambda m: "主要股東持股變動（2016年公司法第138條）"),
    (r"^Changes in Director'?s Interest \(S135\)\s*[-–]?\s*(.*)$",
     lambda m: ("董事持股變動（第135條）" + ("— " + m.group(1) if m.group(1).strip() else ""))),
    (r"^(.+?)\s*[-–]\s*Notice of Book Closure$", lambda m: m.group(1) + " — 閉市過戶通知"),
    (r"^Notice of Book Closure$", lambda m: "閉市過戶通知"),
    (r"^Income Distribution$", lambda m: "收益分派"),
    (r"^Corporate Presentation (.+)$", lambda m: "企業簡報 " + m.group(1)),
    (r"^Annual Audited Accounts?(.*)$", lambda m: "年度經審計財報" + m.group(1)),
    (r"^Annual Report(.*)$", lambda m: "年報" + m.group(1)),
    (r"^General Meetings?:\s*Outcome of Meeting(.*)$", lambda m: "股東大會：會議結果" + m.group(1)),
    (r"^Notice of (?:Interest )?Sub\.? S-hldr.*$", lambda m: "主要股東權益通知"),
    (r"^Dealings in Listed Securities(.*)$", lambda m: "上市證券交易" + m.group(1)),
    (r"^Change in Boardroom(.*)$", lambda m: "董事會人事變動" + m.group(1)),
    (r"^Change in Audit Committee(.*)$", lambda m: "審計委員會變動" + m.group(1)),
    (r"^Change of Company Secretary(.*)$", lambda m: "公司秘書變動" + m.group(1)),
    (r"^OTHERS$", lambda m: "其他"),
]

MONTHS = {"Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05", "Jun": "06",
          "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12"}

RATINGS = {"Buy": "買進", "Strong Buy": "強力買進", "Hold": "中立", "Neutral": "中立",
           "Sell": "賣出", "Strong Sell": "強力賣出", "Outperform": "優於大盤",
           "Underperform": "落後大盤", "Overweight": "加碼", "Underweight": "減碼"}


def _org(name):
    key = name.strip().upper()
    for k, v in ORGS.items():
        if k in key:
            return v
    return name.strip()


# ---------------------------------------------------------------- 快取
_cache = None
_calls = 0                 # 這一輪已經打了幾次 API
MAX_CALLS = 450            # 上限：翻譯服務掛掉時不要把整個更新拖死。
                           # 第一次跑要翻上百篇內文會超過這個數，翻不完的部分
                           # 因為失敗不入快取，下一輪（每 3 小時）會自動補完。
BODY_BUDGET = 300          # 內文最多用到這裡，剩下的留給標題。
                           # 標題是首頁一眼看到的，內文點開時瀏覽器也能現翻；
                           # 公告從 6 則加到 12 則那次，額度被內文用光，138 個標題留英文。


def _load_cache():
    global _cache
    if _cache is None:
        try:
            with open(CACHE_PATH, encoding="utf-8") as fh:
                _cache = json.load(fh)
        except Exception:
            _cache = {}
    return _cache


def save_cache():
    if _cache is None:
        return
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as fh:
        json.dump(_cache, fh, ensure_ascii=False, indent=0, sort_keys=True)


# ---------------------------------------------------------------- 機器翻譯
# 每次請求最多等幾秒。3 小時那支沿用 20 秒；每 5 分鐘的快速新聞更新（news_job.py）改成 8 秒——
# 翻譯服務卡住時，一句等 20 秒、一句換三個引擎，一輪就要好幾分鐘，會拖到下一輪
HTTP_TIMEOUT = 20
# 翻譯的截止時間（time.time() 的秒數）。None = 不限（3 小時那支）。
# 快速新聞更新設成「開始翻譯後 40 秒」：過了就不再打 API、只查快取，
# 批次翻譯、逐句翻譯、逐句翻譯裡換引擎重試，全都看這一個時間
DEADLINE = None


def _late():
    return DEADLINE is not None and time.time() > DEADLINE


def _http(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT,
                                context=ssl.create_default_context()) as r:
        return r.read().decode("utf-8", "replace")


# 每輪各引擎成功／失敗次數，印在更新紀錄最後。
# 2026-09-30 靠這個才發現：雲端（GitHub 機房）呼叫 translate.googleapis.com
# 151 次全部失敗，伺服器端的譯文其實全是 MyMemory 翻的（「Foreign Selling」→「國外銷售」）。
STATS = {}
_streak = {}               # 連續失敗次數；連續失敗太多次就不再叫這個引擎，省時間也省額度


def _count(name, ok):
    key = name if ok else name + " 失敗"
    STATS[key] = STATS.get(key, 0) + 1
    _streak[name] = 0 if ok else _streak.get(name, 0) + 1


def _google_dict(text):
    """Google 的另一個入口（Chrome 字典擴充功能用的）。

    translate.googleapis.com 被限流時，這個入口仍然可用，譯文品質一樣。
    回傳格式是 ["譯文"]，有時是 [["譯文", "en"]]。
    """
    url = ("https://clients5.google.com/translate_a/t?client=dict-chrome-ex"
           "&sl=en&tl=zh-TW&q=" + urllib.parse.quote(text))
    try:
        data = json.loads(_http(url))
        first = data[0]
        if isinstance(first, list):
            first = first[0]
        out = str(first).strip()
    except Exception:
        _count("Google", False)
        raise
    _count("Google", True)
    return out


def _google(text):
    url = ("https://translate.googleapis.com/translate_a/single"
           "?client=gtx&sl=en&tl=zh-TW&dt=t&q=" + urllib.parse.quote(text))
    try:
        data = json.loads(_http(url))
    except Exception:
        _count("Google gtx", False)
        raise
    _count("Google gtx", True)
    out = "".join(seg[0] for seg in data[0] if seg and seg[0])
    return out.strip()


def _mymemory(text):
    url = ("https://api.mymemory.translated.net/get?langpair=en|zh-TW&q="
           + urllib.parse.quote(text))
    try:
        data = json.loads(_http(url))
        if str(data.get("responseStatus")) != "200":
            raise RuntimeError(data.get("responseDetails", "mymemory failed"))
    except Exception:
        _count("MyMemory", False)
        raise
    _count("MyMemory", True)
    return (data["responseData"]["translatedText"] or "").strip()


ENGINES = [("Google", _google_dict), ("Google gtx", _google), ("MyMemory", _mymemory)]


def _engines():
    """依序可用的翻譯引擎。連續失敗 8 次的就跳過（通常是整個被擋），不要每句都等它逾時。"""
    return [fn for name, fn in ENGINES if _streak.get(name, 0) < 8]



# 引擎翻得對、但不是馬股慣用語的詞，翻完再換。
# 跟 PHRASES 不同，這些是中文換中文，不會打斷英文句子的結構。
POST = [
    (r"(外國|海外|外資)(投資者)?(的)?拋售(壓力)?", "外資賣壓"),
    (r"拋壓壓力|拋售壓力", "賣壓"),
    (r"逢低吸收", "逢低買盤"),
    # 標題大寫的「Foreign Selling」會被當成「海外銷售」；股市新聞裡不會是這個意思
    (r"(國外|海外|外國)銷售", "外資賣壓"),
    (r"馬來西亞證券交易所|大馬交易所", "馬交所"),
]


def _polish(text):
    """機器翻譯後的收尾：中文標點後不該有空格，單位縮寫補成中文。"""
    t = text
    for pat, zh in POST:
        t = re.sub(pat, zh, t)
    # 還原佔位符後常留下「，  Sentral」這種標點後的空格
    t = re.sub(r"([，。、；：！？（）「」])\s+", r"\1", t)
    t = re.sub(r"\s+([，。、；：！？）」])", r"\1", t)
    # 金額單位：MT 常常整段留著不翻
    # 注意不能用 \b：中文字也算 word character，"SEN分配" 之間沒有邊界
    end = r"(?![A-Za-z])"
    t = re.sub(r"(\d[\d,.]*)\s*mil" + end, r"\1百萬", t, flags=re.I)
    t = re.sub(r"(\d[\d,.]*)\s*bil" + end, r"\1十億", t, flags=re.I)
    t = re.sub(r"(\d[\d,.]*)\s*sen" + end, r"\1仙", t, flags=re.I)
    # 連續空格收乾淨
    t = re.sub(r"[ \t]{2,}", " ", t).strip()
    return t


def _machine(text):
    """先把公司名與金融慣用語換成佔位符，翻完再換回來。

    佔位符的原因：如果直接把中文塞進英文句子（「Bursa ends 大幅收低 on ...」），
    句子變成中英混雜，翻譯引擎會搞不清楚要翻什麼，後半段乾脆不翻。
    用佔位符的話，引擎看到的是完整英文句子結構，翻完的語序才對。

    還原失敗（佔位符被引擎吃掉）就放棄，回傳 None 保留英文。
    """
    protected, holder = [], text      # protected: 每個佔位符要還原成的文字

    # 1) 金融慣用語 → 還原時換成正確中文
    for pat, zh in PHRASES:
        def sub(m, zh=zh):
            token = "ZZ{}ZZ".format(len(protected))
            protected.append(m.expand(zh).strip())
            return token
        holder = pat.sub(sub, holder)

    # 2) 公司名與縮寫 → 還原時保持英文原樣
    for term in sorted(TERMS, key=len, reverse=True):
        # 短詞一定要加 \b，否則 "RM" 會配到 "farm"、"ROE" 會配到 "Roe"
        pat = re.escape(term)
        if term[:1].isalnum():
            pat = r"\b" + pat
        if term[-1:].isalnum():
            pat = pat + r"\b"
        if not re.search(pat, holder, re.I):
            continue
        token = "ZZ{}ZZ".format(len(protected))
        holder = re.sub(pat, token, holder, flags=re.I)
        protected.append(term)

    for backend in _engines():
        if _late():            # 過了截止時間：這句就算了，不再換下一個引擎重試
            return None
        try:
            out = backend(holder)
        except Exception:
            continue
        if not out:
            continue
        # 佔位符必須原封不動回來，否則翻譯把它吃掉了，寧可退回英文
        ok = all("ZZ{}ZZ".format(i) in out for i in range(len(protected)))
        if not ok:
            continue
        # 檢查要在還原之前做：還原後的中文可能全是對照表自己塞的，
        # 翻譯引擎其實整句沒翻（半英文的結果也會被當成成功）
        engine_part = re.sub(r"ZZ\d+ZZ", "", out)
        if not re.search(r"[一-鿿]", engine_part):
            continue
        if len(re.findall(r"[A-Za-z]{4,}", engine_part)) > 3:
            continue          # 引擎產出裡還留著一堆英文單字，視為沒翻完
        for i, term in enumerate(protected):
            out = out.replace("ZZ{}ZZ".format(i), term)
        return _polish(out)

    # 備援：佔位符被翻譯引擎吃掉（實測 MyMemory 常丟掉句首的佔位符）時，
    # 改成只把慣用語直接換成中文、公司名不保護，再試一次。
    # 語序可能沒那麼好，但總比整句退回英文強。
    plain = _phrases(text)
    for backend in _engines():
        if _late():
            return None
        try:
            out = backend(plain)
        except Exception:
            continue
        if out and re.search(r"[一-鿿]", out):
            return _polish(out)
    return None


# ---------------------------------------------------------------- 對外
def filing_title(text):
    """Bursa 公告標題：先走規則，規則沒中才丟去機器翻譯。"""
    t = (text or "").strip()
    if not t:
        return t
    for pattern, repl in FILING_RULES:
        m = re.match(pattern, t, re.I)
        if m:
            return repl(m)
    if t.startswith("News Release"):
        return "新聞稿 " + text_zh(t[len("News Release"):].strip())
    return text_zh(t)


def category(text):
    return CATEGORY.get((text or "").strip(), text)


def rating(text):
    return RATINGS.get((text or "").strip(), text)


def date_zh(text):
    """'Aug 20, 2026' → '2026-08-20'，看不懂就原樣回傳。"""
    m = re.match(r"^([A-Z][a-z]{2})\w*\s+(\d{1,2}),\s*(\d{4})$", (text or "").strip())
    if m and m.group(1) in MONTHS:
        return "{}-{}-{:02d}".format(m.group(3), MONTHS[m.group(1)], int(m.group(2)))
    return text


def text_zh(text, delay=0.35):
    """一般英文句子 → 中文。有快取就用快取，翻不出來就保留原文。"""
    t = (text or "").strip()
    if not t or not re.search(r"[A-Za-z]{3}", t):
        return t
    if re.search(r"[一-鿿]", t):           # 已經是中文
        return t

    cache = _load_cache()
    if t in cache:
        return cache[t]

    global _calls
    if _calls >= MAX_CALLS or _late():             # 翻譯服務出問題、或時間到了，直接停手
        return t
    if not _engines():
        # 每個引擎都連續失敗到被跳過了：_machine 不會打任何請求，
        # 但下面還是會停 0.35 秒——上百句就白等半分鐘
        return t
    _calls += 1

    out = _machine(t)
    time.sleep(delay)                              # 對免費端點客氣一點
    if out:
        cache[t] = out
        return out
    # 翻失敗不寫進快取：可能只是暫時限流，下次更新再試一次，
    # 若寫進去就會被永久記成英文，再也不會重翻
    return t


def trad(text):
    """簡體中文轉繁體（星洲日報等大馬中文媒體是簡體）。失敗就原樣回傳，簡體也看得懂。"""
    t = (text or "").strip()
    if not t or not re.search(r"[一-鿿]", t):
        return t
    cache = _load_cache()
    key = "簡→繁|" + t
    if key in cache:
        return cache[key]
    url = ("https://clients5.google.com/translate_a/t?client=dict-chrome-ex"
           "&sl=zh-CN&tl=zh-TW&q=" + urllib.parse.quote(t))
    try:
        data = json.loads(_http(url))
        first = data[0][0] if isinstance(data[0], list) else data[0]
        out = str(first).strip()
    except Exception:
        return t
    if out:
        cache[key] = out
        return out
    return t


def trad_many(texts, max_url=6000, keep_failed=True):
    """一次把很多句簡體轉成繁體，回傳同樣順序的 list。

    每 5 分鐘的快速新聞更新（news_job.py）一輪有上百則中文報標題，
    像 trad() 那樣一句打一次 API 要幾十秒，而且容易被限流。
    clients5 可以在同一個網址帶多個 q，一次回傳一整個陣列，所以分批送：
    網址太長會被拒，每批控制在 max_url 字元內。
    某一批失敗就那批保留簡體（簡體也看得懂），不寫進快取，下一輪再試。
    keep_failed=False 時沒轉成的回傳 None：呼叫端要分得出「轉好了（剛好沒有字要換）」
    和「沒轉成」——news_job.py 不能把簡體當成繁體譯文發出去，要讓網頁自己轉。
    過了 DEADLINE 或連續兩批連不上就不再送（服務掛了，後面幾批也一樣）。
    """
    cache = _load_cache()
    out = [(t or "").strip() for t in texts]
    todo = []
    for t in out:
        if t and re.search(r"[一-鿿]", t) and "簡→繁|" + t not in cache and t not in todo:
            todo.append(t)

    base = "https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl=zh-CN&tl=zh-TW"
    batches, cur, size = [], [], len(base)
    for t in todo:
        q = "&q=" + urllib.parse.quote(t)
        if cur and size + len(q) > max_url:
            batches.append(cur)
            cur, size = [], len(base)
        cur.append(t)
        size += len(q)
    if cur:
        batches.append(cur)

    down = 0
    for batch in batches:
        if _late() or down >= 2:
            break
        url = base + "".join("&q=" + urllib.parse.quote(t) for t in batch)
        try:
            data = json.loads(_http(url))
            # 回傳 ["繁1", "繁2", ...]；偶爾每句會包成 ["繁1", "zh-CN"]
            got = [str(d[0] if isinstance(d, list) else d).strip() for d in data]
            if len(batch) == 1:           # 單句時也可能回 ["繁1", "zh-CN"]
                got = got[:1]
            down = 0
        except Exception:
            _count("Google 簡→繁", False)
            down += 1
            continue
        if len(got) != len(batch):        # 對不上就整批不用，免得張冠李戴
            _count("Google 簡→繁", False)
            continue
        _count("Google 簡→繁", True)
        for src, dst in zip(batch, got):
            if dst:
                cache["簡→繁|" + src] = dst

    if not keep_failed:
        return [cache.get("簡→繁|" + t) if t and re.search(r"[一-鿿]", t) else t for t in out]
    return [cache.get("簡→繁|" + t, t) if t else t for t in out]


def _protect(text):
    """跟 _machine 第一步一樣：慣用語、公司名換成佔位符。回傳 (換好的句子, 每個佔位符要還原成的文字)。"""
    protected, holder = [], text
    for pat, zh in PHRASES:
        def sub(m, zh=zh):
            token = "ZZ{}ZZ".format(len(protected))
            protected.append(m.expand(zh).strip())
            return token
        holder = pat.sub(sub, holder)
    for term in sorted(TERMS, key=len, reverse=True):
        pat = re.escape(term)
        if term[:1].isalnum():
            pat = r"\b" + pat
        if term[-1:].isalnum():
            pat = pat + r"\b"
        if not re.search(pat, holder, re.I):
            continue
        token = "ZZ{}ZZ".format(len(protected))
        holder = re.sub(pat, token, holder, flags=re.I)
        protected.append(term)
    return holder, protected


def _restore(out, protected):
    """跟 _machine 一樣的驗收標準，通過才還原佔位符；不通過回傳 None。"""
    if not out or not all("ZZ{}ZZ".format(i) in out for i in range(len(protected))):
        return None
    engine_part = re.sub(r"ZZ\d+ZZ", "", out)
    if not re.search(r"[一-鿿]", engine_part):
        return None
    if len(re.findall(r"[A-Za-z]{4,}", engine_part)) > 3:
        return None
    for i, term in enumerate(protected):
        out = out.replace("ZZ{}ZZ".format(i), term)
    return _polish(out)


def half_done(src, zh):
    """英文標題的譯文是不是半成品（沒翻或只翻了一點）。

    跟 _restore 同一個標準：拿掉保護的專有名詞（Maybank、REIT、RM⋯⋯）之後，
    要有中文，而且 4 個字母以上的英文字不能超過 3 個。
    「新聞稿 」是 filing_title 自己加的前綴，不算翻譯出來的中文——
    後面整句翻失敗時會變成「新聞稿 Sentral REIT 1H 2026 Realised Net Income rises⋯」，
    看起來有中文，其實沒翻。
    news_job.py 用上一輪的 news.json 當快取時靠這個挑掉半成品，這一輪重翻。
    """
    src, body = (src or "").strip(), (zh or "").strip()
    if not body or body == src:
        return True
    if src.startswith("News Release") and body.startswith("新聞稿"):
        body = body[len("新聞稿"):]
    _, protected = _protect(src)
    # 只拿掉英文的專有名詞。慣用語的中文（Final Dividend →「末期股息」）是正當的譯文，
    # 拿掉的話整句只剩空白，會被當成沒翻
    for term in sorted((p for p in protected if re.search(r"[A-Za-z]", p)), key=len, reverse=True):
        body = re.sub(re.escape(term), "", body, flags=re.I)
    if not re.search(r"[一-鿿]", body):
        return True
    return len(re.findall(r"[A-Za-z]{4,}", body)) > 3


def text_zh_many(texts, max_url=6000):
    """很多句英文一次翻完，回傳同樣順序的 list。

    text_zh 一句打一次 API、每句還要停 0.35 秒；每 5 分鐘的快速新聞更新（news_job.py）
    一輪可能有上百則新標題，逐句翻要一兩分鐘。clients5 一個網址可以帶多個 q，
    所以把佔位符換好的句子分批送，每句再用跟 _machine 一樣的標準驗收
    （佔位符都在、真的翻成中文、沒剩一堆英文）。
    沒通過的原樣回傳英文、不寫進快取，呼叫端可以再逐句用 text_zh 補。
    一批算一次 MAX_CALLS 額度。過了 DEADLINE 或連續兩批連不上就不再送。
    """
    cache = _load_cache()
    out = [(t or "").strip() for t in texts]
    todo = {}
    for t in out:
        if (not t or t in cache or t in todo or not re.search(r"[A-Za-z]{3}", t)
                or re.search(r"[一-鿿]", t)):
            continue
        todo[t] = _protect(t)

    def send(items):
        """items: [(原文, 要送出的句子)]，分批送，回傳 {原文: 引擎譯文}。"""
        base = "https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl=en&tl=zh-TW"
        batches, cur, size = [], [], len(base)
        for t, q in items:
            n = len("&q=" + urllib.parse.quote(q))
            if cur and size + n > max_url:
                batches.append(cur)
                cur, size = [], len(base)
            cur.append((t, q))
            size += n
        if cur:
            batches.append(cur)

        global _calls
        got, down = {}, 0
        for batch in batches:
            if _calls >= MAX_CALLS or _late() or down >= 2:
                break
            _calls += 1
            url = base + "".join("&q=" + urllib.parse.quote(q) for _, q in batch)
            try:
                data = json.loads(_http(url))
                res = [str(d[0] if isinstance(d, list) else d).strip() for d in data]
                if len(batch) == 1:
                    res = res[:1]
                down = 0
            except Exception:
                _count("Google 批次", False)
                down += 1
                continue
            if len(res) != len(batch):        # 對不上就整批不用，免得張冠李戴
                _count("Google 批次", False)
                continue
            _count("Google 批次", True)
            got.update({t: r for (t, _), r in zip(batch, res)})
        return got

    # 第一輪：佔位符保護過的句子
    first = send([(t, holder) for t, (holder, _) in todo.items()])
    retry = []
    for t in todo:
        zh = _restore(first.get(t), todo[t][1])
        if zh:
            cache[t] = zh
        elif t in first:
            retry.append(t)
    # 第二輪：跟 _machine 的備援一樣——佔位符被吃掉或留太多英文的，
    # 改成只把慣用語換成中文、公司名不保護，有翻成中文就收
    for t, zh in send([(t, _phrases(t)) for t in retry]).items():
        if zh and re.search(r"[一-鿿]", zh):
            cache[t] = _polish(zh)

    return [cache.get(t, t) if t else t for t in out]


def paragraph_zh(text, chunk=450):
    """長文分段翻譯。

    切塊上限 450 字元是被 MyMemory 逼出來的——它免費版單次查詢上限 500 bytes，
    超過直接回錯誤。Google 沒這個限制，但它是會限流的那個，所以以最嚴的為準。
    切在句號處，不切在半句中間。
    """
    t = (text or "").strip()
    if not t or not re.search(r"[A-Za-z]{3}", t):
        return t

    cache = _load_cache()
    if t in cache:
        return cache[t]

    parts, buf = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", t):
        if len(buf) + len(sentence) > chunk and buf:
            parts.append(buf.strip())
            buf = ""
        buf += sentence + " "
    if buf.strip():
        parts.append(buf.strip())

    if _calls + len(parts) > BODY_BUDGET:   # 翻不完就別開始，額度留給標題
        return t

    out = []
    for part in parts:
        zh = text_zh(part)
        if zh == part:              # 這一段沒翻成功，整篇放棄比半中半英好
            return t
        out.append(zh)

    joined = "".join(out)
    cache[t] = joined
    return joined


if __name__ == "__main__":
    import sys
    for line in sys.argv[1:]:
        print(line, "→", filing_title(line))
    save_cache()
