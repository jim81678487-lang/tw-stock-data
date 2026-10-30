# 台股每日資料

每天台灣時間 07:00 自動從 FinMind 抓取台股資料，計算均線、資金面與動能指標，存到 `data/latest.json` 與 `data/latest.md`，供 Claude 每天早上的台股晨報讀取。

## 內容

大盤
- 加權指數與櫃買指數：季線、半年線、乖離、距歷史高點回檔、崩盤警示燈
- 三大法人整體買賣超、整體融資餘額變化

個股（持股與觀察名單）
- 均線：季線與半年線乖離、連續站上或跌破的天數
- 資金面：外資、投信、自營商買賣超與連買連賣天數，融資融券餘額變化，外資持股比例
- 動能：1 日、5 日、20 日漲跌幅，RSI，量比

崩盤警示燈：紅燈 距歷史高點跌逾 20%；橙燈 跌破半年線；黃燈 跌破季線；綠燈 站上季線。

## 設定步驟

1. 申請 FinMind 免費帳號：到 https://finmindtrade.com 註冊並驗證信箱，登入後在使用者資訊頁複製 API Token。
2. 申請 GitHub 帳號，按右上角加號選 New repository，名稱填 `tw-stock-data`，選 Public，按 Create repository。
3. 在新專案頁面按 uploading an existing file，把 `fetch_data.py`、`config.json`、`README.md` 拖進去，按 Commit changes。
4. 建立排程檔：按 Add file，選 Create new file，檔名輸入 `.github/workflows/daily.yml`，把 `daily.yml` 的內容整份貼上，按 Commit changes。
5. 存放 Token：到 Settings，選 Secrets and variables，再選 Actions，按 New repository secret，Name 填 `FINMIND_TOKEN`，Secret 貼上第 1 步的 Token。
6. 第一次執行：到 Actions 分頁，左邊選 Daily TW stock data，按 Run workflow。約 3 分鐘後出現綠色勾勾，`data` 資料夾裡會有 `latest.md`。
7. 把專案網址傳給 Claude，例如 `https://github.com/你的帳號/tw-stock-data`。

## 修改持股或觀察名單

直接在 GitHub 上編輯 `config.json`，新增或刪除股票代號與名稱，按 Commit changes 即可，隔天自動生效。

## 常見問題

- 執行失敗出現紅色叉叉：點進去看錯誤訊息。若是推送權限錯誤，到 Settings，Actions，General，把 Workflow permissions 改成 Read and write permissions。
- 專案設為 Public，代表任何人都看得到你的股票清單，但看不到持股數量與 Token。若想設為 Private，需要另外在 claude.ai 連結 GitHub 帳號，晨報才讀得到。
