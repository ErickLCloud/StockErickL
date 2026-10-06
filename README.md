# StockErickL — 台股 ETF 看板

可搜尋、可點選的台股 ETF 與個股看板。盤中由 GitHub Actions 每 5 分鐘更新，
靜態網頁部署在 GitHub Pages。

網頁：<https://ericklcloud.github.io/StockErickL/>

> 第一次部署前要先手動開啟 Pages：repo → **Settings → Pages** → Source 選
> **Deploy from a branch**，branch `main`、資料夾 **`/web`**。

## 為什麼是「近即時」而不是即時

TWSE 與 TPEx 的公開端點**都沒有開放 CORS**（2026-10-06 實測，四個端點皆無
`Access-Control-Allow-Origin`）。靜態網站沒有後端，瀏覽器的每個請求都是跨來源，
所以**頁面無法直接呼叫這些 API** —— 即使用 curl 或 Python 抓得到也一樣。

因此抓取改在**伺服器端**進行：GitHub Actions 定時抓資料、寫成 JSON commit 進 repo，
網頁再讀同源的 JSON。代價是延遲：

- 盤中每 5 分鐘更新一次（GitHub 排程為 best-effort，壅塞時常延遲 5–15 分鐘，也可能跳過）
- 頁面會顯示它**實際顯示的那份資料的時間戳**，所以資料過期是看得見的，不會被藏起來

要真正秒級即時，需要額外架一個會補上 CORS 標頭的 proxy（例如 Cloudflare Worker），
那就不只是 GitHub 了。

## 架構

```
docs/index.html            單檔靜態頁，無任何 CDN 依賴（圖表是內嵌 SVG）
docs/data/index.json       universe 清單 + 每檔最後收盤價（pc）
docs/data/quotes.json      盤中價格（唯一的「熱」檔案，每 5 分鐘重寫）
docs/data/indicators.json  技術指標值（每日重算）
docs/data/history/<code>.json  每檔 250 天 OHLCV，供繪圖
docs/data/market.json      加權指數
scripts/build_web_data.py 產生上述 JSON
.github/workflows/intraday.yml  每 5 分鐘，不需資料庫
.github/workflows/daily.yml     每日重建歷史與指標
```

`quotes.json` 刻意只放會變動的欄位 —— 名稱/板別在 `index.json`、指標在
`indicators.json`。否則每 5 分鐘重寫一次會讓 repo 被無意義的 diff 塞爆
（165KB → 45KB）。

同理，5 分鐘的那個 job **不需要資料庫**：universe 與備援收盤價都從已 commit 的
`index.json` 讀。這點已實測（把 `stock.db` 改名後 `quotes` 模式仍正常）。

## 本機使用

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

.\.venv\Scripts\python.exe main.py setup      # 首次：建 universe + 2 年回補
.\.venv\Scripts\python.exe main.py report     # 產生每日 Markdown 報告
.\.venv\Scripts\python.exe main.py query 0050 # 單一標的
.\.venv\Scripts\python.exe scripts\build_web_data.py full   # 產生網頁資料

# 本機預覽網頁（直接用 file:// 開會被瀏覽器擋）
.\.venv\Scripts\python.exe -m http.server -d docs 8000
```

## 資料來源

| 用途 | 來源 | 需要金鑰 |
|---|---|---|
| 上市日線 | TWSE OpenAPI `STOCK_DAY_ALL` | 否 |
| 上櫃日線 | TPEx OpenAPI | 否 |
| 本益比/殖利率/PB | TWSE `BWIBBU_ALL`（**僅上市個股**） | 否 |
| 盤中報價 | TWSE MIS（單次最多 100 檔） | 否 |
| 加權指數 | TWSE `MI_INDEX` | 否 |
| 歷史回補 | yfinance（`.TW` / `.TWO`） | 否 |

全部免註冊、免 API token。

## 已知限制

- **ETF 沒有基本面資料。** `BWIBBU_ALL` 對 357 檔 ETF 覆蓋率為 **0%**（個股 94.9%）。
  TWSE 全部 143 個端點中沒有 ETF 淨值/折溢價端點，PE/PB 本質上也是個股概念。
  頁面一律顯示 `—`，不是 bug。
- **357 檔中只有 303 檔算得出 MA240**（年線）。其餘是新上市 ETF，歷史不足；
  指標逐項降級為 `—`，不會整檔失效。
- **至少 5 檔有未調整的分割／反分割跳空**（`00673R` 恰好 1:4.00、`0052` ~1:6.99、
  `00663L` ~1:7.31、`00887` 兩次 ~1:2）。跨跳空的 MA240 與報酬率會失真。
  槓桿（L）/反向（R）ETF 最常見。**尚未修正。**
- **278/357 檔有盤中報價**，其餘 MIS 不提供，頁面退回顯示最後收盤值並標示「收盤值」。
- 盤中報價來自 TWSE MIS，該端點並非正式文件化的 API，可能變動。

## 免責

僅供個人參考，不構成投資建議。

## 色彩慣例

紅漲綠跌（台股慣例，與美股相反）。
