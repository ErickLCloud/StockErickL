# StockErickL — 台股看板

可搜尋、可點選的台股看板，涵蓋**上市櫃全部個股與 ETF（約 2,400 檔）**。
盤中每 5 分鐘更新報價，網頁部署在 GitHub Pages。

網頁：<https://ericklcloud.github.io/StockErickL/>

## 功能

- **搜尋**：輸入代號或名稱（`2330`、`台積`）。完全相符的代號排最前面。
- **篩選與排序**：ETF／個股、上漲、下跌、自選；依代號、漲幅、跌幅、成交量。
- **明細**：現價、漲跌、開高低、成交量；個股另有本益比、殖利率、股價淨值比；
  MA5／20／60／240、KD、RSI14、MACD 柱、量比；1 月到 1 年的走勢圖。
- **自選與持倉**：做在網頁上，**只存在你的瀏覽器**（localStorage）。
  持倉會即時算市值、損益、報酬率與年化（持有滿 30 天才顯示年化），可匯出／匯入備份。
- 紅漲綠跌（台股慣例），深色模式自動跟隨系統。

## 第一次部署（只做一次）

1. repo → **Settings → Pages → Build and deployment → Source** 選 **GitHub Actions**。
2. repo → **Actions → site → Run workflow**，`mode` 選 **full**（約 10–15 分鐘，會建立歷史資料）。
3. 完成後網址即上線。之後排程自動維護：盤中每 5 分鐘更新報價，每個交易日 15:37（台北）重建歷史。

> 只跑 `quotes` 而沒跑過 `full` 時，頁面仍可用，但走勢圖與指標會顯示「無歷史資料」。

## 為什麼是「近即時」，以及為什麼資料不放進 git

TWSE 與 TPEx 的公開端點**都沒有開放 CORS**（2026-10-06 實測），瀏覽器無法直接呼叫。
所以抓取改在伺服器端（GitHub Actions）進行，網頁再讀同源的 JSON。代價：

- GitHub 排程是 best-effort，壅塞時常延遲 5–15 分鐘，也可能跳過。
- 頁面標題列會顯示它**實際顯示的那份報價的時間**與「N 分鐘前」，過期是看得見的。

全市場報價每次約 300 KB，若每 5 分鐘 commit 一次，一天就約 70 個 commit，每個還會觸發一次 Pages 建置。
所以**生成資料完全不進 `main`**：workflow 直接把成品部署到 Pages；慢的歷史資料每天重建一次，
存在一個每天覆寫成單一 commit 的孤立 `data` 分支（不累積歷史）。

要真正秒級即時，需要額外一個會補上 CORS 標頭的 proxy（例如 Cloudflare Worker），那就不只是 GitHub 了。

## 資料來源（全部免註冊、免金鑰）

| 用途 | 來源 |
|---|---|
| 上市／上櫃名單與收盤 | TWSE `STOCK_DAY_ALL`、TPEx `tpex_mainboard_quotes` |
| 盤中報價 | TWSE MIS（單次最多 100 檔，全市場 24 個請求） |
| 本益比／殖利率／淨值比 | TWSE `BWIBBU_ALL`（上市）、TPEx `peratio_analysis`（上櫃） |
| 加權指數 | TWSE `MI_INDEX` |
| 2 年歷史 | yfinance（`.TW` / `.TWO`） |

## 結構

```
docs/index.html, docs/calc.js   頁面（單檔、無 CDN 依賴，圖表是內嵌 SVG）與純邏輯
scripts/build_web_data.py       quotes（只用標準函式庫）/ history（pandas + yfinance）
scripts/sync_publish.py         把可公開的部分鏡像到 publish/（預設 dry-run）
.github/workflows/site.yml      建置並部署到 Pages
src/  tests/  main.py           本機分析（SQLite、每日 Markdown 報告），見下
```

`docs/data/` 是建置產物，已 gitignore。

## 本機使用

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

.\.venv\Scripts\python.exe scripts\build_web_data.py quotes    # 約 90 秒
.\.venv\Scripts\python.exe scripts\build_web_data.py history   # 約 10 分鐘
.\.venv\Scripts\python.exe -m http.server -d docs 8000         # 開 http://127.0.0.1:8000/
# 直接用 file:// 開會被瀏覽器擋下

.\.venv\Scripts\python.exe main.py report      # 每日 Markdown 報告（用本機 SQLite）
.\.venv\Scripts\python.exe main.py query 0050  # 單一標的

.\.venv\Scripts\python.exe -m pytest tests/ -q
```

網頁邏輯另有瀏覽器內的測試：`tests/web/selftest.html`（計算與搜尋）與
`tests/web/e2e.html`（對真實頁面與真實資料操作搜尋、自選、持倉）。
用任一 http 伺服器提供專案根目錄，再以 headless Edge／Chrome 開啟並 `--dump-dom`，
看輸出裡的 `RESULT: PASS`。注意每次要用全新的瀏覽器設定檔，否則舊的 HTTP 快取會讓測試驗到過期資料。

## 已知限制

- **ETF 沒有基本面。** TWSE 全部 143 個端點中沒有 ETF 淨值／折溢價；PE／PB 本質上是個股概念。顯示 `—`。
- **「ETF」是用代號前綴 `00` 判斷的**，會一併納入債券 ETF 等；免費端點沒有商品類別欄位。
- **價格異常跳空的標的會被截斷。** 單日收盤比超出 0.70–1.45 倍（台股漲跌幅限制 ±10%，不可能是正常交易）
  就視為公司行動或資料錯誤。Yahoo 的 `Adj Close` 在這些點上**沒有**調整（實測比值恆為 ×1.000），
  所以無從由資料還原分割比例；與其猜測，不如只用最後一次跳空之後的資料算指標與畫圖，並在頁面標示。
  目前全市場約 29 檔（約 1.2%）受影響，其中部分可能是真實的劇烈行情而非分割，無法由資料判斷。
- **指標需要歷史長度。** 約 2,300/2,400 檔算得出 MA240，新上市者逐項顯示 `—`。
- **盤中報價約 85%（2,035/2,393）有成交**，其餘退回顯示最後收盤並標示「收盤值」。
- 盤中報價來自 TWSE MIS，該端點並非正式文件化的 API，可能變動。
- 損益**未含手續費與證交稅**。

## 免責

僅供個人參考，不構成投資建議。
