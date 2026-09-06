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

> https://evolee1100.github.io/stockchecker/

排程寫在 `.github/workflows/daily.yml`，每週一到週五 18:30（馬來西亞時間，馬股 5pm 收盤後）跑一次：
抓價量 → 抓基本面 → 抓新聞 → 把快照存回 repo → 重新部署網頁。

想立刻更新一次不用等排程：

```bash
gh workflow run daily.yml --repo evolee1100/stockchecker
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

## 翻譯

英文資訊會轉成繁體中文，分三層處理：

1. **規則表** — Bursa 公告標題是套版的，用正規表示式對照翻譯，比機器翻譯準且不會失敗
2. **機器翻譯** — 新聞標題是自由文字，Google 為主、MyMemory 備援
3. **保留原文** — 兩者都失敗就顯示英文，不會出現半截或亂碼

公司名與金融縮寫（OPR、NIM、DPU、EPF⋯）在送去翻譯前會換成佔位符保護，翻完還原；
**還原失敗就整句退回英文**。翻譯結果快取在 `data/translations.json` 並進版控，
所以每天只需要翻新增的標題。翻譯失敗不寫進快取，下次更新會再試一次。

網頁上每個區塊標題旁有「原文」按鈕可以切回英文核對。

Yahoo 的 `quoteSummary` / `v7/quote` 端點現在需要認證，抓不到基本面，所以基本面走第二個來源。若該來源失效，價量仍會正常更新，只是基本面欄位會空著。

## 儀表板會自動標出的訊號

- 創 52 週新低 / 距高點超過 15%
- 單日漲跌超過 ±2%
- 成交量放大 2 倍以上（配合下跌時特別值得注意）
- 近 30 天內除息（解釋「無故下跌」很多時候就是這個）
- P/B < 1、ROE 高低、派息率超過 100%

## 油棕（棕櫚油期貨）

Bursa 的 FCPO（馬幣計價）沒有可用的免費來源——官網、stooq、investing.com 都擋機器人。
所以用 CME 的美元合約 `CPO=F` 乘上 `USD/MYR` 匯率換算成 RM/噸。
**這是換算值，不是 FCPO 結算價**，介面上有標明。

另外 `CPO=F` 的 `meta.regularMarketPrice` 是 2024 年的死資料，只能取每日序列的最後一筆。

展開棕櫚油那一列有「月價試算」：

```
30日均價 × 30 ÷ 係數 = 月價
4,752.02 × 30 ÷ 18.6 = RM 7,664.55
```

30 日均價是近 30 個交易日收盤價加總除以 30。三個欄位都可以改，係數會記在瀏覽器裡。

## 展開單一個股

點表格任一列會展開：走勢圖（可切 1個月 / 3個月 / 半年 / 1年，滑鼠移過去會顯示該日價格）、完整數據、**媒體報導**與 **Bursa 官方公告**兩欄並排。公告是第一手的「到底發生什麼事」，報導則有解讀與脈絡。

## 注意

資料為收盤價，不是即時報價。本工具只做資訊整理，不構成投資建議；下單前請以 Bursa 公告與券商資料為準。
