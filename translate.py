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
MAX_CALLS = 200            # 上限：翻譯服務掛掉時不要把整個更新拖死


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
def _http(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20,
                                context=ssl.create_default_context()) as r:
        return r.read().decode("utf-8", "replace")


def _google(text):
    url = ("https://translate.googleapis.com/translate_a/single"
           "?client=gtx&sl=en&tl=zh-TW&dt=t&q=" + urllib.parse.quote(text))
    data = json.loads(_http(url))
    out = "".join(seg[0] for seg in data[0] if seg and seg[0])
    return out.strip()


def _mymemory(text):
    url = ("https://api.mymemory.translated.net/get?langpair=en|zh-TW&q="
           + urllib.parse.quote(text))
    data = json.loads(_http(url))
    if str(data.get("responseStatus")) != "200":
        raise RuntimeError(data.get("responseDetails", "mymemory failed"))
    return (data["responseData"]["translatedText"] or "").strip()



def _polish(text):
    """機器翻譯後的收尾：中文標點後不該有空格，單位縮寫補成中文。"""
    t = text
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
    """公司名先換成佔位符，翻完再換回來；換不回來就放棄，回傳 None 保留英文。"""
    protected, holder = [], text
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

    for backend in (_google, _mymemory):
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
        for i, term in enumerate(protected):
            out = out.replace("ZZ{}ZZ".format(i), term)
        if re.search(r"[一-鿿]", out):     # 真的有中文才算成功
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
    if _calls >= MAX_CALLS:                        # 翻譯服務出問題時直接停手
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


if __name__ == "__main__":
    import sys
    for line in sys.argv[1:]:
        print(line, "→", filing_title(line))
    save_cache()
