"""
Meta 廣告素材抓取器 v3.0 — 歐盟/英國廣告 + 國外標註
好創整合行銷 | 子權 2026-06-28

核心邏輯：
- 搜尋英國(GB)廣告（Meta Ad Library API 政策：只有英國/歐盟可抓商業廣告）
- 所有 Meta 來源自動標註「🌍 國外廣告（英國）」
- 每次執行自動選「下一個還沒跑滿的產業」
- 每個產業每輪抓 5 支投放最久的廣告

重要說明：
- Meta Ad Library API 台灣只能抓政治廣告
- 商業廣告只能抓英國/歐盟（Meta 政策限制）
- 所有 Meta 廣告在 Notion/Slack 中均標示「🌍 國外廣告（英國）」
- 這些廣告用於學習創意手法，不代表台灣市場現況

產業輪流狀態儲存在：data/meta_industry_progress.json
"""

import os
import json
import time
import random
import subprocess
import requests
from datetime import datetime, timedelta
from pathlib import Path

# ─── 設定區 ───────────────────────────────────────────────
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
SEARCH_COUNTRY    = "GB"       # 英國（Meta 政策：只有英國/歐盟可抓商業廣告）
SEARCH_AD_TYPE    = "ALL"
ADS_PER_INDUSTRY  = 5          # 每個產業每輪抓幾支
MAX_QUERY_LIMIT   = 25         # 每次 API 查詢最多取幾筆
TOTAL_ROUNDS      = 2          # 總輪數
COUNTRY_LABEL     = "🌍 國外廣告（英國）"   # 所有 Meta 廣告統一標註

# 動態計算路徑，避免硬編碼 /home/ubuntu 導致環境移植失敗
BASE_DIR       = Path(__file__).resolve().parent
DATA_DIR       = BASE_DIR / "data"
PROGRESS_FILE  = DATA_DIR / "meta_industry_progress.json"

# ─── 英國市場產業清單（英文關鍵字）────────────────────────
# 按廣告庫存量排序，確保前幾個產業一定抓得到
INDUSTRY_LIST = [
    # ── 美妝個人護理 ──
    {"name": "美容保養",   "keywords": ["skincare", "moisturizer", "serum", "beauty", "anti-aging"]},
    {"name": "彩妝美髮",   "keywords": ["makeup", "lipstick", "foundation", "hair dye", "mascara"]},
    {"name": "醫美診所",   "keywords": ["aesthetic clinic", "botox", "filler", "laser treatment", "skin clinic"]},
    # ── 健康醫療 ──
    {"name": "健身減重",   "keywords": ["fitness", "weight loss", "gym", "workout", "fat burning"]},
    {"name": "保健食品",   "keywords": ["supplements", "protein powder", "vitamins", "probiotics", "collagen"]},
    {"name": "牙科診所",   "keywords": ["dental", "teeth whitening", "braces", "orthodontics", "dentist"]},
    # ── 飲食餐飲 ──
    {"name": "餐飲食品",   "keywords": ["restaurant", "food delivery", "meal kit", "takeaway", "healthy food"]},
    {"name": "機能飲品",   "keywords": ["coffee", "energy drink", "protein shake", "smoothie", "tea"]},
    # ── 服飾生活 ──
    {"name": "服飾配件",   "keywords": ["fashion", "clothing", "shoes", "handbag", "accessories"]},
    {"name": "家具家居",   "keywords": ["furniture", "home decor", "sofa", "mattress", "interior design"]},
    {"name": "寵物",       "keywords": ["pet food", "dog", "cat", "pet accessories", "pet care"]},
    # ── 教育知識 ──
    {"name": "線上課程",   "keywords": ["online course", "e-learning", "skill training", "certification", "masterclass"]},
    {"name": "語言學習",   "keywords": ["language learning", "English course", "Spanish", "language app", "IELTS"]},
    {"name": "職涯發展",   "keywords": ["career coaching", "resume", "job skills", "professional development", "LinkedIn"]},
    # ── 財務金融 ──
    {"name": "投資理財",   "keywords": ["investment", "stocks", "crypto", "passive income", "financial freedom"]},
    {"name": "保險金融",   "keywords": ["insurance", "life insurance", "mortgage", "pension", "savings"]},
    # ── 科技數位 ──
    {"name": "3C電子",     "keywords": ["smartphone", "laptop", "headphones", "tablet", "smart home"]},
    {"name": "軟體服務",   "keywords": ["app", "software", "subscription", "SaaS", "digital tool"]},
    {"name": "AI工具",     "keywords": ["AI tool", "artificial intelligence", "ChatGPT", "automation", "AI software"]},
    # ── 旅遊娛樂 ──
    {"name": "旅遊住宿",   "keywords": ["travel", "hotel", "holiday", "flights", "vacation"]},
    # ── 電商零售 ──
    {"name": "電商零售",   "keywords": ["sale", "discount", "limited offer", "free shipping", "buy now"]},
    # ── 婚慶 ──
    {"name": "婚禮婚慶",   "keywords": ["wedding", "engagement ring", "wedding dress", "wedding venue", "bridal"]},
]


# ─── 進度管理 ─────────────────────────────────────────────

def load_progress() -> dict:
    """載入產業輪流進度"""
    DATA_DIR.mkdir(exist_ok=True)
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "round": 1,
        "current_industry_index": 0,
        "industries": [i["name"] for i in INDUSTRY_LIST],
        "industry_stats": {
            i["name"]: {"round1": 0, "round2": 0, "processed_ids": []}
            for i in INDUSTRY_LIST
        }
    }


def save_progress(progress: dict):
    """儲存進度"""
    DATA_DIR.mkdir(exist_ok=True)
    progress["updated_at"] = datetime.now().isoformat()
    PROGRESS_FILE.write_text(
        json.dumps(progress, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def get_next_industry(progress: dict) -> dict | None:
    """取得下一個要執行的產業"""
    current_round = progress["round"]
    if current_round > TOTAL_ROUNDS:
        return None

    round_key = f"round{current_round}"
    industries = progress["industries"]
    idx = progress["current_industry_index"]

    for offset in range(len(industries)):
        i = (idx + offset) % len(industries)
        name = industries[i]
        stats = progress["industry_stats"].get(name, {})
        if stats.get(round_key, 0) < ADS_PER_INDUSTRY:
            progress["current_industry_index"] = i
            return next((ind for ind in INDUSTRY_LIST if ind["name"] == name), None)

    if current_round < TOTAL_ROUNDS:
        progress["round"] = current_round + 1
        progress["current_industry_index"] = 0
        save_progress(progress)
        return get_next_industry(progress)

    return None


def mark_industry_done(progress: dict, industry_name: str, count: int, new_ids: list):
    """記錄某產業本輪完成的數量"""
    current_round = progress["round"]
    round_key = f"round{current_round}"
    stats = progress["industry_stats"].setdefault(
        industry_name, {"round1": 0, "round2": 0, "processed_ids": []}
    )
    stats[round_key] = stats.get(round_key, 0) + count
    stats["processed_ids"] = list(set(stats.get("processed_ids", []) + new_ids))

    if stats[round_key] >= ADS_PER_INDUSTRY:
        industries = progress["industries"]
        current_idx = progress["current_industry_index"]
        progress["current_industry_index"] = (current_idx + 1) % len(industries)

    save_progress(progress)


# ─── 廣告過濾 ─────────────────────────────────────────────

def is_valid_ad(ad: dict, processed_ids: set) -> bool:
    """判斷廣告是否值得拆解"""
    ad_id = ad.get("id", "")
    if ad_id in processed_ids:
        return False
    if not ad.get("is_active", False):
        return False
    videos = ad.get("videos", []) or []
    if not videos:
        return False
    start_date_str = ad.get("ad_delivery_start_time", "")
    if start_date_str:
        try:
            start_date = datetime.fromisoformat(start_date_str.replace("Z", "+00:00"))
            days = (datetime.now(start_date.tzinfo) - start_date).days
            if days < 7:   # 英國廣告放寬到7天（市場較小）
                return False
        except Exception:
            pass
    return True


def get_days_running(ad: dict) -> int:
    """計算廣告投放天數"""
    start_date_str = ad.get("ad_delivery_start_time", "")
    if not start_date_str:
        return 0
    try:
        start_date = datetime.fromisoformat(start_date_str.replace("Z", "+00:00"))
        return (datetime.now(start_date.tzinfo) - start_date).days
    except Exception:
        return 0


def extract_video_url(ad: dict) -> str:
    """從廣告資料中提取影片 URL"""
    videos = ad.get("videos", []) or []
    for v in videos:
        if isinstance(v, dict):
            url = v.get("video_url") or v.get("url") or ""
            if url and not url.startswith("MOCK"):
                return url
    return ad.get("snapshot_url", "")


# ─── Meta Ad Library API ──────────────────────────────────

def search_meta_ads(keyword: str, limit: int = MAX_QUERY_LIMIT) -> list:
    """呼叫 Meta Ad Library API（搜尋英國廣告）"""
    if not META_ACCESS_TOKEN:
        print(f"  ⚠️  無 META_ACCESS_TOKEN，使用模擬資料")
        return _get_mock_ads(keyword)

    url = "https://graph.facebook.com/v25.0/ads_archive"
    params = {
        "access_token": META_ACCESS_TOKEN,
        "ad_type": SEARCH_AD_TYPE,
        "ad_reached_countries": f'["{SEARCH_COUNTRY}"]',
        "search_terms": keyword,
        "fields": ",".join([
            "id", "ad_creative_bodies", "ad_creative_link_captions",
            "ad_creative_link_urls", "ad_delivery_start_time",
            "ad_delivery_stop_time", "is_active", "page_name",
            "page_id", "videos", "snapshot_url",
        ]),
        "limit": limit,
    }
    try:
        resp = requests.get(url, params=params, timeout=30)
        data = resp.json()

        # 檢查是否有錯誤
        if "error" in data:
            err = data["error"]
            err_msg = err.get("error_user_msg") or err.get("message", "未知錯誤")
            err_code = err.get("code", 0)
            print(f"  ❌ Meta API 錯誤（{keyword}）：{err_msg}")
            # Token 過期（190）或權限不足（10）：發送 Slack 警告給子權
            if err_code in [10, 190]:
                _notify_token_expired(err_msg, err_code)
                print(f"  ⚠️  Token 過期或權限不足，使用模擬資料")
                return _get_mock_ads(keyword)
            return []

        return data.get("data", [])

    except requests.exceptions.RequestException as e:
        print(f"  ❌ Meta API 連線失敗（{keyword}）：{e}")
        return []


# 已發送警告的 flag，同一次執行只發一次
_token_alert_sent = False


def _notify_token_expired(err_msg: str, err_code: int):
    """發送 Slack DM 給子權，通知 Meta Token 需要更新"""
    global _token_alert_sent
    if _token_alert_sent:
        return  # 同一次執行只發一次
    _token_alert_sent = True
    slack_token = os.environ.get("SLACK_BOT_TOKEN", "")
    ziquan_id   = os.environ.get("SLACK_ZIQUAN_ID", "U07MHPJKQ8V")
    if not slack_token:
        print(f"  ⚠️  Meta Token 過期（錯誤碼 {err_code}），但 SLACK_BOT_TOKEN 未設定，無法發送警告")
        return
    msg = (
        f"🚨 *Meta Access Token 需要更新*\n"
        f"錯誤碼：{err_code} | {err_msg}\n"
        f"請前往 Meta Business Manager 重新產生 Token，\n"
        f"再更新 `/home/ubuntu/viral_factory/.env` 的 `META_ACCESS_TOKEN` 欄位"
    )
    cmd = [
        "manus-mcp-cli", "tool", "call", "slack_send_dm",
        "--server", "slack",
        "--input", json.dumps({"user_id": ziquan_id, "message": msg})
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        print(f"  📨 Meta Token 過期警告已發送給子權")
    except Exception as e:
        print(f"  ⚠️  Slack 警告發送失敗：{e}")


def _get_mock_ads(keyword: str) -> list:
    """模擬廣告資料（無 Token 或權限不足時使用）"""
    base_days = [72, 55, 48, 38, 31, 22, 18]
    return [
        {
            "id": f"mock_{keyword}_{i:03d}",
            "page_name": f"UK {keyword} Brand {chr(65+i)}",
            "ad_delivery_start_time": (datetime.now() - timedelta(days=base_days[i % len(base_days)])).isoformat(),
            "is_active": True,
            "ad_creative_bodies": [f"[{keyword}] Mock Ad #{i+1} - UK Market"],
            "ad_creative_link_urls": ["https://example.com"],
            "videos": [{"video_url": f"MOCK_{keyword}_{i:03d}"}],
            "snapshot_url": "https://www.facebook.com/ads/library/",
            "_mock": True,
        }
        for i in range(7)
    ]


# ─── 主函數 ───────────────────────────────────────────────

def get_meta_ad_urls_by_industry() -> list:
    """
    主函數：按產業輪流模式抓取高成效廣告（英國市場）

    Returns:
        list of dict: [{"url", "ad_id", "page_name", "days_running",
                        "industry", "round", "is_mock", "country_label",
                        "source_note"}]
    """
    progress = load_progress()
    industry = get_next_industry(progress)

    if industry is None:
        print("\n✅ 所有產業兩輪已完成！")
        print("   如需繼續，請手動重置 data/meta_industry_progress.json")
        return []

    current_round = progress["round"]
    industry_name = industry["name"]
    keywords = industry["keywords"]

    stats = progress["industry_stats"].get(industry_name, {})
    processed_ids = set(stats.get("processed_ids", []))

    print(f"\n{'='*55}")
    print(f"Meta 廣告抓取 | 第 {current_round} 輪 | 產業：{industry_name}")
    print(f"來源國家：英國（GB）| 標註：{COUNTRY_LABEL}")
    print(f"目標：{ADS_PER_INDUSTRY} 支 | 已處理：{len(processed_ids)} 支（歷史去重）")
    print(f"{'='*55}")

    candidates = []
    for keyword in keywords:
        print(f"  搜尋「{keyword}」...")
        ads = search_meta_ads(keyword)
        for ad in ads:
            if is_valid_ad(ad, processed_ids):
                days = get_days_running(ad)
                video_url = extract_video_url(ad)
                if video_url:
                    candidates.append({
                        "url": video_url,
                        "ad_id": ad.get("id", ""),
                        "page_name": ad.get("page_name", "Unknown Brand"),
                        "days_running": days,
                        "industry": industry_name,
                        "round": current_round,
                        "is_mock": ad.get("_mock", False),
                        "snapshot_url": ad.get("snapshot_url", ""),
                        "country_label": COUNTRY_LABEL,          # 國外廣告標註
                        "source_note": f"Meta廣告庫（英國）| {industry_name}",
                    })
        time.sleep(random.uniform(0.3, 0.8))

    # 去重
    seen_ids = set()
    unique_candidates = []
    for c in candidates:
        if c["ad_id"] not in seen_ids:
            seen_ids.add(c["ad_id"])
            unique_candidates.append(c)

    # 按投放天數排序
    unique_candidates.sort(key=lambda x: x["days_running"], reverse=True)
    selected = unique_candidates[:ADS_PER_INDUSTRY]

    # 更新進度
    new_ids = [s["ad_id"] for s in selected]
    mark_industry_done(progress, industry_name, len(selected), new_ids)

    print(f"\n篩選結果：{len(selected)} 支（共找到 {len(unique_candidates)} 支有效廣告）")
    for i, item in enumerate(selected, 1):
        mock_tag = "（模擬）" if item["is_mock"] else ""
        print(f"  {i}. {item['page_name']}{mock_tag} | {item['country_label']}")
        print(f"     投放天數：{item['days_running']}天 | {item['url'][:50]}...")

    _print_overall_progress(progress)
    return selected


def get_meta_ad_urls_simple() -> list:
    """簡化版：回傳廣告資訊字典清單（供 daily_run.py 使用）"""
    items = get_meta_ad_urls_by_industry()
    # 回傳所有廣告（含模擬），但標記 is_mock
    return items


def get_progress_summary() -> str:
    """回傳進度摘要字串（供 Slack 日報使用）"""
    progress = load_progress()
    current_round = progress["round"]
    industries = progress["industries"]
    round_key = f"round{current_round}"

    done = sum(
        1 for name in industries
        if progress["industry_stats"].get(name, {}).get(round_key, 0) >= ADS_PER_INDUSTRY
    )
    total = len(industries)

    return (
        f"Meta廣告進度（英國來源）：第{current_round}輪 {done}/{total} 個產業完成\n"
        f"每產業目標：{ADS_PER_INDUSTRY}支 × {TOTAL_ROUNDS}輪 = {ADS_PER_INDUSTRY * TOTAL_ROUNDS}支\n"
        f"標註：{COUNTRY_LABEL}"
    )


def _print_overall_progress(progress: dict):
    """列印整體進度表"""
    print(f"\n{'─'*55}")
    print(f"整體進度（目標：每產業 {ADS_PER_INDUSTRY * TOTAL_ROUNDS} 支）")
    print(f"{'─'*55}")
    for name in progress["industries"]:
        stats = progress["industry_stats"].get(name, {})
        r1 = stats.get("round1", 0)
        r2 = stats.get("round2", 0)
        total = r1 + r2
        bar = "█" * total + "░" * (ADS_PER_INDUSTRY * TOTAL_ROUNDS - total)
        print(f"  {name:<8} [{bar}] {total}/{ADS_PER_INDUSTRY * TOTAL_ROUNDS}")
    print(f"{'─'*55}\n")


# ─── 執行入口 ─────────────────────────────────────────────

if __name__ == "__main__":
    print("測試 Meta 廣告素材抓取器 v3.0（英國市場）...")
    results = get_meta_ad_urls_by_industry()
    print(f"\n本次抓取：{len(results)} 支廣告")
    print(f"\n{get_progress_summary()}")
