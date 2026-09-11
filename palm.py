#!/usr/bin/env python3
"""MPOB 官方每日馬來西亞毛棕櫚油（CPO）價格。

來源：https://bepi.mpob.gov.my/admin2/price_local_daily_view_cpo_msia.php
這是馬來西亞棕油局的官方牌價，RM/噸，比拿 CME 美元合約換算準得多
（原本用 CPO=F x USD/MYR，那是換算值不是官方價）。

價格藏在頁面的 Highcharts 設定裡：categories 是日期、data 是價格。
"""

import re
import ssl
import urllib.request
from datetime import datetime

URL = ("https://bepi.mpob.gov.my/admin2/price_local_daily_view_cpo_msia.php"
       "?more=Y&jenis={span}")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")

MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def _fetch(span):
    req = urllib.request.Request(URL.format(span=span), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30,
                                context=ssl.create_default_context()) as r:
        return r.read().decode("utf-8", "replace")


def _parse(page):
    cat = re.search(r"categories\s*:\s*\[(.*?)\]", page, re.S)
    dat = re.search(r"data\s*:\s*\[([\d.,\s]+)\]", page, re.S)
    if not (cat and dat):
        return []

    # 日期本身含逗號（'Sep 03, 2026'），所以要抓引號內的整段，不能用逗號切
    dates = re.findall(r"'([^']+)'", cat.group(1))
    vals = [float(x) for x in re.findall(r"\d+\.?\d*", dat.group(1))]

    out = []
    for d, v in zip(dates, vals):
        m = re.match(r"^([A-Z][a-z]{2})\s+(\d{1,2}),\s*(\d{4})$", d.strip())
        if not m or m.group(1) not in MONTHS:
            continue
        iso = "{}-{:02d}-{:02d}".format(m.group(3), MONTHS[m.group(1)], int(m.group(2)))
        out.append({"date": iso, "close": round(v, 2)})
    out.sort(key=lambda x: x["date"])
    return out


def daily_prices(span="6M"):
    """回傳 [{date, close}]，close 是 RM/噸。"""
    return _parse(_fetch(span))


if __name__ == "__main__":
    s = daily_prices()
    print("{} 筆，{} → {}".format(len(s), s[0]["date"], s[-1]["date"]))
    print("最新 RM {:,.2f}/噸".format(s[-1]["close"]))
    w = [p["close"] for p in s[-30:]]
    print("30 日均價 RM {:,.2f}".format(sum(w) / len(w)))
    print("× 出油率 18.75% = 鮮果串 RM {:,.2f}/噸".format(sum(w) / len(w) * 0.1875))
