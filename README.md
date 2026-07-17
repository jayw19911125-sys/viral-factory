# 好創爆款短影音拆解工廠

自動監控台灣 TikTok 爆款帳號，每日拆解 6 支異常飆高影片，寫入 Notion 靈感庫並通知阿韋。

## P0 資料安全模式

2026-07-17 起預設啟用 `VIRAL_SAFE_MODE=true`：現行流程只有音訊轉錄，尚未取得真實影片畫面，因此影片會標記為 `quarantined`，不呼叫 AI、不寫入 Notion、不發企劃／剪輯建議。這是防止把缺少證據的內容擴散到 02、03–07 與 35 庫的發布閘門。

只有完成並驗證畫面抽幀／Vision 階段後，才能解除安全模式。單純修改環境變數不能把 `visual_ready` 變成真；程式會依實際 evidence contract 判斷。

批次 outcome 分為：

- `unique_success`：唯一新素材，Notion 建立後已 read-back。
- `duplicate`：canonical video ID／URL 已存在。
- `quarantined`：證據不足，不發布建議。
- `queued`：只進本地待重送佇列，不算入庫。
- `write_unverified`：Notion 可能部分建立，但無法 read-back。
- `technical_error`：來源、下載、轉錄、AI、Notion 或分配錯誤。

Slack 狀態另行區分 `trackable_sent`（取得 channel_id + message_ts）、
`sent_untracked`（只有成功碼，不能追 thread）與 `confirmed`（指定收件人在同一
message_ts 的 thread 回覆精確 `OK`）。這些事件都不代表已讀或工作完成。

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

每週只允許一個獨立排程執行：
weekly_report.py → 跨影片規律分析 → Slack #all-團隊主頻道
```

`RUN_WEEKLY_INSIDE_DAILY` 預設為 `false`，避免 daily job 與 Manus 週報排程重複發送。週報只有在觀看、作者、Hook、結構與證據覆蓋率均達門檻時才產生建議；否則只發資料阻擋狀態。

Notion 02 庫需要以下結構化欄位：`入庫日期`、`觀看數`、`作者帳號`、`評分`、`platform_video_id`、`run_id`、`處理狀態`、`證據狀態`。

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

# 收集可追蹤訊息的 thread ACK；應在通知合理等待時間後獨立排程
python3 ack_collector.py
```

## 環境變數需求

```
# 語音轉文字使用沙盒內建 manus-speech-to-text，GPT（gpt-4.1-mini）走沙盒代理，
# 皆不需要 OpenAI API Key。以下為選用：
OPENAI_API_KEY=sk-proj-xxx    # 選用：直接呼叫 OpenAI API 時才需要
META_ACCESS_TOKEN=xxx         # 選用：Meta 廣告庫抓取（缺少則僅拆解有機內容）
VIRAL_SAFE_MODE=true          # 預設 true；P0 期間不得自動發布 AI 建議
RUN_WEEKLY_INSIDE_DAILY=false # 週報只保留一個獨立排程
AUTO_UPDATE_MANUAL=false      # 正式 SOP 不由每日資料任務自動改寫
SEND_MANAGEMENT_REPORT_IMMEDIATELY=false # 等 ACK collector 後再發管理報
```

## 驗證

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile data_quality.py viral_factory.py daily_run.py weekly_report.py ack_collector.py
```

## 手動待拆清單

將影片 URL 貼入 `manual_queue.txt`（每行一個），下次執行時自動優先拆解。
系統先 peek、不預先刪除；只有 `unique_success` 或已證實 `duplicate` 才從清單移除。
`quarantined`、`queued`、`write_unverified` 與 `technical_error` 都會保留待重試。
