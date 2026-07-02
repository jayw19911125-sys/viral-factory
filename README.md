# 好創爆款短影音拆解工廠

自動監控台灣 TikTok 爆款帳號，每日拆解 6 支異常飆高影片，寫入 Notion 靈感庫並通知阿韋。

## 系統架構

```
TikTok 9 個監控帳號
    ↓ 每日 09:00（台灣時間）
trending_fetcher.py → 掃描最新影片，偵測 48 小時異常飆高
    ↓
viral_factory.py → 下載音頻 → manus-speech-to-text 轉文字 → GPT（gpt-4.1-mini）拆解
    ↓
Notion 爆款拆解庫（ID：82097a06-fae5-83bd-a8c3-87236d3713aa）
    ↓
Slack DM 阿韋（每日通知）

每週五 09:00 額外執行：
weekly_report.py → 跨影片規律分析 → Slack #all-團隊主頻道
```

## 監控帳號（9 個）

| 類別 | TikTok 帳號 |
| :--- | :--- |
| 搞笑/劇情 | @specsome, @nonstop_rave, @zuibabibi_ |
| 自媒體/短影音教學 | @wille_wang, @lin.na_8 |
| 創業/商業思維 | @enfin0529, @ujay1103 |
| 劇情/微電影 | @chuanlee666666, @tongsyue |

## 執行方式

```bash
# 於專案目錄下執行（路徑自動偵測，不需固定安裝位置）

# 每日拆解（排程自動執行）
python3 daily_run.py

# 手動觸發單次拆解
python3 daily_run.py

# 手動觸發週報分析
python3 weekly_report.py
```

## 環境變數需求

```
# 語音轉文字使用沙盒內建 manus-speech-to-text，GPT（gpt-4.1-mini）走沙盒代理，
# 皆不需要 OpenAI API Key。以下為選用：
OPENAI_API_KEY=sk-proj-xxx    # 選用：直接呼叫 OpenAI API 時才需要
META_ACCESS_TOKEN=xxx         # 選用：Meta 廣告庫抓取（缺少則僅拆解有機內容）
```

## 手動待拆清單

將影片 URL 貼入 `manual_queue.txt`（每行一個），下次執行時自動優先拆解。
