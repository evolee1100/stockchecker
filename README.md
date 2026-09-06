# 馬股每日追蹤 (stockchecker)

追蹤馬來西亞銀行股、REIT 與大盤的每日儀表板。純 Python 標準函式庫 + 一頁 HTML，**不需要 pip install，也不需要 API 金鑰**。

## 快速開始

```bash
python3 fetch.py     # 抓最新資料
./serve.sh           # 開伺服器，然後瀏覽器打開 http://localhost:8777
```

`index.html` 也可以直接雙擊開啟（資料同時寫成 `data/data.js`，所以 file:// 也讀得到）。

## 在手機上看（人不在電腦旁）

網頁掛在 GitHub Pages，**每天由 GitHub 在雲端自動更新，不需要你的 Mac 開機**：

> https://rootedfutures3.github.io/stockchecker/

排程寫在 `.github/workflows/daily.yml`，每週一到週五 18:30（馬來西亞時間，馬股 5pm 收盤後）跑一次：
抓價量 → 抓基本面 → 抓新聞 → 把快照存回 repo → 重新部署網頁。

想立刻更新一次不用等排程：

```bash
gh workflow run daily.yml --repo rootedfutures3/stockchecker
```

或在 GitHub 網頁的 Actions 分頁按 **Run workflow**。

## 設定每天自動更新（本機，選用）

上面的雲端排程已經夠用了。如果你還想讓 Mac 自己也留一份本機資料：

```bash
./install_daily.sh          # 每天 18:30（馬股 5pm 收盤後）
./install_daily.sh 20 00    # 或改成每天 20:00
```

（Mac 關機或睡眠時這個不會跑，所以出門在外請看上面的 GitHub Pages 網址。）

## 檔案說明

| 檔案 | 用途 |
|---|---|
| `watchlist.json` | **要增減股票就改這個**。每檔需要 `symbol`（Yahoo 代碼，Bursa 為 `代碼.KL`）與 `sa`（stockanalysis 的英文代號，用來抓基本面）|
| `fetch.py` | 抓價量、算漲跌幅與訊號，寫出 `data/data.json` 與 `data/data.js` |
| X、P/B、ROE、殖利率、派息率、分析師目標價 |
| `index.html` | 儀表板本體（單一檔案，無外部相依）|
| `data/history/` | 每天一份快照，之後可以回頭比對 |

## 資料來源

- **價量、52週高低、除息紀錄** — Yahoo Finance chart API
- **基本面** — stockanalysis.com
- **媒體報導** — Google News RSS（The Edge、The Star、NST、Bernama、EdgeProp⋯）
- **官方公告** — klsescreener 轉載的 Bursa 公告（財報、派息、股權變動）

Yahoo 的 `quoteSummary` / `v7/quote` 端點現在需要認證，抓不到基本面，所以基本面走第二個來源。若該來源失效，價量仍會正常更新，只是基本面欄位會空著。

## 儀表板會自動標出的訊號

- 創 52 週新低 / 距高點超過 15%
- 單日漲跌超過 ±2%
- 成交量放大 2 倍以上（配合下跌時特別值得注意）
- 近 30 天內除息（解釋「無故下跌」很多時候就是這個）
- P/B < 1、ROE 高低、派息率超過 100%

## 展開單一個股

點表格任一列會展開：走勢圖（可切 1個月 / 3個月 / 半年 / 1年）、完整數據、**媒體報導**與 **Bursa 官方公告**兩欄並排。公告是第一手的「到底發生什麼事」，報導則有解讀與脈絡。

## 注意

資料為收盤價，不是即時報價。本工具只做資訊整理，不構成投資建議；下單前請以 Bursa 公告與券商資料為準。
