"""
爆款短影音監控系統 v3.0 - 熱門榜單抓取器
好創整合行銷 | 子權 2026-06-08

核心邏輯：
1. 每日掃描所有監控帳號的最新影片與觀看數
2. 將數據寫入每週累積資料庫（JSON）
3. 偵測 48 小時內觀看數異常飆高的影片（超過該帳號平均值 3 倍）
4. 按異常程度排序，取前 50 支送入拆解流程
5. 每日拆解目標：6 支（每週 30 支）

資料儲存：
- <專案目錄>/data/weekly_YYYYWW.json  每週累積數據
- <專案目錄>/data/snapshots/YYYYMMDD_HH.json  每次快照
"""

import subprocess
import json
import time
import random
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from data_quality import canonical_video_identity, load_processed_registry, optional_int

# ─── 設定 ─────────────────────────────────────────────────
DAILY_TARGET        = 6       # 每日送入拆解的影片數
ANOMALY_MULTIPLIER  = 3.0     # 觀看數超過帳號平均值幾倍才算異常
ANOMALY_WINDOW_HOURS = 48     # 異常偵測時間窗口（小時）
# 動態計算路徑，避免硬編碼 /home/ubuntu 導致環境移植失敗
BASE_DIR            = Path(__file__).resolve().parent
MONITOR_FILE        = BASE_DIR / "monitor_accounts.json"
DATA_DIR            = BASE_DIR / "data"
SNAPSHOT_DIR        = DATA_DIR / "snapshots"
LOG_DIR             = BASE_DIR / "logs"
MANUAL_QUEUE_FILE   = BASE_DIR / "manual_queue.txt"
PROCESSED_REGISTRY  = DATA_DIR / "processed_videos.json"

# ─── 工具函數 ─────────────────────────────────────────────

def load_monitor_accounts() -> list:
    """載入監控帳號清單"""
    with open(MONITOR_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)
    accounts = config.get("tiktok_accounts", [])
    # 若有 IG cookie，也加入 IG 帳號
    ig_accounts = config.get("instagram_accounts", [])
    return accounts + ig_accounts


def fetch_account_videos(account: dict, max_count: int = 10) -> list:
    """
    抓取單一帳號的最新影片清單（不下載，只取 metadata）

    修復缺陷8：yt-dlp --flat-playlist 模式下 view_count 幾乎永遠是 0，
    因為 TikTok 的觀看數需要實際請求影片頁面才能取得。

    改為兩段式抓取：
    1. --flat-playlist 快速取得 URL + title 清單
    2. 對每支影片單獨執行 yt-dlp --dump-json 取得真實 view_count
       （只取前 max_count 支，每支 timeout 20 秒，失敗則 view_count=None）

    回傳：[{"url": "...", "title": "...", "view_count": N, "timestamp": "..."}]
    """
    # ── 第一段：取 URL + title 清單 ──
    cmd_list = [
        "yt-dlp",
        "--flat-playlist",
        "--playlist-end", str(max_count),
        "--print", "%(webpage_url)s\t%(title)s\t%(timestamp)s",
        "--no-warnings",
        "--ignore-errors",
        account["url"]
    ]
    result = subprocess.run(cmd_list, capture_output=True, text=True, timeout=45)
    raw_videos = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 1:
            continue
        url = parts[0].strip()
        if not url.startswith("http"):
            continue
        title = parts[1].strip() if len(parts) > 1 else ""
        timestamp = parts[2].strip() if len(parts) > 2 else ""
        raw_videos.append({"url": url, "title": title, "timestamp": timestamp})

    # ── 第二段：對每支影片取真實 view_count ──
    videos = []
    for v in raw_videos[:max_count]:
        view_count = None
        # 加入重試機制：yt-dlp 抹取 TikTok 觀看數失敗率高，最多重試 2 次
        for attempt in range(2):
            try:
                cmd_detail = [
                    "yt-dlp",
                    "--dump-json",
                    "--no-playlist",
                    "--no-warnings",
                    "--ignore-errors",
                    v["url"]
                ]
                detail_result = subprocess.run(
                    cmd_detail, capture_output=True, text=True, timeout=20
                )
                if detail_result.returncode == 0 and detail_result.stdout.strip():
                    info = json.loads(detail_result.stdout.strip().split("\n")[0])
                    view_count = optional_int(info.get("view_count"))
                    # 若 title 為空，用 detail 補充
                    if not v["title"]:
                        v["title"] = info.get("title", "")
                    break  # 成功就不再重試
                elif attempt == 0:
                    time.sleep(random.uniform(1.0, 2.5))  # 第一次失敗等待後重試
            except Exception as e:
                if attempt == 0:
                    time.sleep(random.uniform(1.0, 2.5))
                else:
                    pass  # 重試兩次都失敗，保留 None，不能冒充真實 0

        videos.append({
            "url": v["url"],
            "title": v["title"],
            "view_count": view_count,
            "timestamp": v["timestamp"],
            "handle": account["handle"],
            "platform": "tiktok" if "tiktok.com" in v["url"] else "instagram",
            "category": account.get("category", ""),
            "fetched_at": datetime.now().isoformat()
        })
    return videos


def save_snapshot(all_videos: list) -> Path:
    """儲存本次抓取快照"""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = SNAPSHOT_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(all_videos, f, ensure_ascii=False, indent=2)
    return snapshot_path


def load_weekly_data() -> dict:
    """載入本週累積數據"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    week_key = datetime.now().strftime("%Y%W")
    weekly_file = DATA_DIR / f"weekly_{week_key}.json"
    if weekly_file.exists():
        with open(weekly_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"week": week_key, "videos": {}}


def save_weekly_data(data: dict):
    """儲存本週累積數據"""
    week_key = datetime.now().strftime("%Y%W")
    weekly_file = DATA_DIR / f"weekly_{week_key}.json"
    with open(weekly_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def update_weekly_data(weekly_data: dict, fresh_videos: list) -> dict:
    """
    將本次抓取的數據合併進每週累積資料庫
    同一支影片若已存在，記錄觀看數歷史（用於計算增長速度）
    """
    now_iso = datetime.now().isoformat()
    for v in fresh_videos:
        identity_data = canonical_video_identity(v["url"])
        identity = identity_data["identity"]
        if identity not in weekly_data["videos"]:
            weekly_data["videos"][identity] = {
                "url": identity_data["canonical_url"],
                "identity": identity,
                "platform_video_id": identity_data["video_id"] or None,
                "title": v["title"],
                "handle": v["handle"],
                "platform": v["platform"],
                "category": v["category"],
                "first_seen": now_iso,
                "view_history": []
            }
        # 記錄觀看數歷史（時間戳 + 觀看數）
        if v["view_count"] is not None:
            weekly_data["videos"][identity]["view_history"].append({
                "ts": now_iso,
                "views": v["view_count"]
            })
    return weekly_data


def detect_anomalies(weekly_data: dict) -> list:
    """
    偵測 48 小時內觀看數異常飆高的影片
    
    異常定義：
    - 在 48 小時內，觀看數增長量 > 該帳號所有影片平均增長量的 ANOMALY_MULTIPLIER 倍
    - 或：最新觀看數 > 10 萬（絕對值門檻）
    
    回傳：按異常程度排序的影片清單
    """
    now = datetime.now()
    cutoff = now - timedelta(hours=ANOMALY_WINDOW_HOURS)
    video_growth = []

    for identity, video in weekly_data.get("videos", {}).items():
        history = [row for row in video.get("view_history", []) if optional_int(row.get("views")) is not None]
        if not history:
            continue
        latest_views = int(history[-1]["views"])
        growth_48h = None
        growth_rate = None

        # A single lifetime-view observation is not 48-hour growth.  It may only
        # satisfy the explicit absolute-view signal.
        if len(history) >= 2:
            old_views = None
            for record in reversed(history[:-1]):
                try:
                    if datetime.fromisoformat(record["ts"]) <= cutoff:
                        old_views = int(record["views"])
                        break
                except (KeyError, TypeError, ValueError):
                    continue
            if old_views is None:
                old_views = int(history[0]["views"])
            growth_48h = latest_views - old_views
            growth_rate = growth_48h / old_views if old_views > 0 else None

        video_growth.append({
            "identity": video.get("identity", identity),
            "url": video.get("url", ""),
            "title": video.get("title", ""),
            "handle": video.get("handle", ""),
            "platform": video.get("platform", ""),
            "category": video.get("category", ""),
            "latest_views": latest_views,
            "growth_48h": growth_48h,
            "growth_rate": growth_rate,
            "anomaly_score": 0.0,
            "signal": None,
        })

    handle_rates = {}
    for row in video_growth:
        if row["growth_rate"] is not None and row["growth_rate"] >= 0:
            handle_rates.setdefault(row["handle"], []).append(row["growth_rate"])
    handle_avg = {
        handle: sum(values) / len(values)
        for handle, values in handle_rates.items()
        if values
    }

    qualified = []
    for row in video_growth:
        avg = handle_avg.get(row["handle"])
        relative_score = None
        if avg and avg > 0 and row["growth_rate"] is not None:
            relative_score = row["growth_rate"] / avg

        if relative_score is not None and relative_score >= ANOMALY_MULTIPLIER:
            row["anomaly_score"] = relative_score
            row["signal"] = "relative_growth"
            qualified.append(row)
        elif row["latest_views"] >= 100000:
            row["anomaly_score"] = row["latest_views"] / 100000
            row["signal"] = "absolute_views"
            qualified.append(row)

    qualified.sort(key=lambda row: (row["anomaly_score"], row["latest_views"]), reverse=True)
    return qualified[:50]


def _atomic_write_manual_queue(lines: list[str]) -> None:
    MANUAL_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="manual_queue_", suffix=".txt", dir=MANUAL_QUEUE_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines).rstrip() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, MANUAL_QUEUE_FILE)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def load_manual_queue(limit: int = DAILY_TARGET) -> list:
    """Peek at most ``limit`` URLs; removal happens only after a terminal result."""
    if not MANUAL_QUEUE_FILE.exists():
        MANUAL_QUEUE_FILE.write_text(
            "# 爆款短影音手動待拆清單\n# 每行一個 URL，# 開頭為註解\n",
            encoding="utf-8"
        )
        return []
    urls = []
    lines = MANUAL_QUEUE_FILE.read_text(encoding="utf-8").strip().split("\n")
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and line.startswith("http"):
            urls.append(line)
    return urls[:limit]


def acknowledge_manual_queue(results: list[dict]) -> int:
    """Remove only URLs proven successful or already present; retain retryable rows."""
    if not MANUAL_QUEUE_FILE.exists():
        return 0
    terminal = {
        row.get("identity")
        for row in results
        if row.get("outcome") in {"unique_success", "duplicate"} and row.get("identity")
    }
    if not terminal:
        return 0

    lines = MANUAL_QUEUE_FILE.read_text(encoding="utf-8").splitlines()
    kept = []
    removed = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("http"):
            try:
                identity = canonical_video_identity(stripped)["identity"]
            except ValueError:
                identity = None
            if identity in terminal:
                removed += 1
                continue
        kept.append(line)
    _atomic_write_manual_queue(kept)
    return removed


def get_daily_urls() -> list:
    """
    主函數：取得今日待拆解的影片 URL 清單
    
    流程：
    1. 抓取所有監控帳號的最新影片
    2. 更新每週累積數據庫
    3. 偵測 48 小時異常飆高的影片
    4. 結合手動清單，取前 DAILY_TARGET 支
    """
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'='*60}")
    print(f"爆款短影音監控系統 | {today}")
    print(f"{'='*60}")
    
    # Step 1：載入監控帳號
    accounts = load_monitor_accounts()
    print(f"\n[1/4] 監控帳號：{len(accounts)} 個")
    
    # Step 2：抓取所有帳號的最新影片
    print(f"\n[2/4] 抓取最新影片數據...")
    all_fresh_videos = []
    for account in accounts:
        try:
            videos = fetch_account_videos(account, max_count=10)
            all_fresh_videos.extend(videos)
            print(f"  @{account['handle']}: {len(videos)} 支")
            time.sleep(random.uniform(0.5, 1.5))
        except Exception as e:
            print(f"  @{account['handle']}: 抓取失敗 ({e})")
    
    print(f"  合計抓取：{len(all_fresh_videos)} 支")
    
    # Step 3：儲存快照 + 更新每週數據
    print(f"\n[3/4] 更新每週累積數據庫...")
    save_snapshot(all_fresh_videos)
    weekly_data = load_weekly_data()
    weekly_data = update_weekly_data(weekly_data, all_fresh_videos)
    save_weekly_data(weekly_data)
    print(f"  本週累積：{len(weekly_data['videos'])} 支影片")
    
    # Step 4：異常偵測 + 排序
    print(f"\n[4/4] 偵測 48 小時異常飆高影片...")
    anomalies = detect_anomalies(weekly_data)
    
    if anomalies:
        print(f"\n  前 10 異常影片：")
        for i, v in enumerate(anomalies[:10], 1):
            growth_text = f"{v['growth_48h']:+,}" if v["growth_48h"] is not None else "無歷史"
            print(f"  {i:2d}. @{v['handle']} | 觀看：{v['latest_views']:,} | "
                  f"48h增長：{growth_text} | 訊號：{v['signal']} | 分數：{v['anomaly_score']:.1f}")
            print(f"      {v['title'][:50]}")
    
    # 手動清單優先
    manual_urls = load_manual_queue(DAILY_TARGET)
    
    # 從異常清單取 URL（去除已在手動清單的）
    manual_set = {canonical_video_identity(url)["identity"] for url in manual_urls}
    processed = load_processed_registry(PROCESSED_REGISTRY)["videos"]
    auto_urls = [
        row["url"]
        for row in anomalies
        if row.get("identity") not in manual_set and row.get("identity") not in processed
    ]
    
    remaining = max(0, DAILY_TARGET - len(manual_urls))
    final_urls = manual_urls + auto_urls[:remaining]
    
    print(f"\n{'='*60}")
    print(f"今日待拆解：{len(final_urls)} 支")
    print(f"  手動：{len(manual_urls)} 支 | 自動（異常偵測）：{len(auto_urls[:remaining])} 支")
    print(f"{'='*60}\n")
    
    return final_urls[:DAILY_TARGET]


if __name__ == "__main__":
    urls = get_daily_urls()
    print("\n最終待拆清單：")
    for i, url in enumerate(urls, 1):
        print(f"  {i}. {url}")
