"""
Meta 廣告素材抓取器 v2.0 — 按產業輪流模式
好創整合行銷 | 子權 2026-06-10

核心邏輯：
- 每次執行自動選「下一個還沒跑滿的產業」
- 每個產業每輪抓 5 支投放最久的廣告
- 兩輪後每產業累積 10 支完整拆解數據
- 所有產業跑完後自動進入第二輪

產業輪流狀態儲存在：data/meta_industry_progress.json
格式：
{
  "round": 1,                         # 目前第幾輪（1 or 2）
  "current_industry_index": 3,        # 目前輪到第幾個產業
  "industries": [...],                # 產業清單
  "industry_stats": {
    "美容保養": {"round1": 5, "round2": 0, "processed_ids": [...]},
    ...
  }
}

無效廣告過濾標準（自動排除）：
- 投放天數 < 14 天（太新，無法判斷成效）
- 無影片素材（純圖片廣告）
- 廣告已停播（is_active = false）

高成效廣告排序依據（投放天數為主）：
- 投放天數越長 = 成效越好 = 優先拆解
"""

import os
import json
import time
import random
import requests
from datetime import datetime, timedelta
from pathlib import Path

# ─── 設定區 ───────────────────────────────────────────────
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
SEARCH_COUNTRY    = "TW"
SEARCH_AD_TYPE    = "ALL"
ADS_PER_INDUSTRY  = 5      # 每個產業每輪抓幾支
MAX_QUERY_LIMIT   = 25     # 每次 API 查詢最多取幾筆（要多抓才夠過濾）
TOTAL_ROUNDS      = 2      # 總輪數（2輪 = 每產業10支）

DATA_DIR       = Path("/home/ubuntu/viral_factory/data")
PROGRESS_FILE  = DATA_DIR / "meta_industry_progress.json"

# ─── 台灣市場產業清單 ─────────────────────────────────────
# 按廣告庫存量排序（多 → 少），確保前幾個產業一定抓得到
INDUSTRY_LIST = [
    {"name": "美容保養",   "keywords": ["保養", "美容", "護膚", "面膜", "精華"]},
    {"name": "醫美診所",   "keywords": ["醫美", "雷射", "微整", "玻尿酸", "縮毛孔"]},
    {"name": "餐飲食品",   "keywords": ["餐廳", "美食", "便當", "飲料", "甜點"]},
    {"name": "服飾配件",   "keywords": ["服飾", "穿搭", "包包", "飾品", "鞋子"]},
    {"name": "健身減重",   "keywords": ["健身", "瘦身", "減脂", "增肌", "運動"]},
    {"name": "線上課程",   "keywords": ["課程", "學習", "培訓", "教學", "技能"]},
    {"name": "電商零售",   "keywords": ["購物", "特賣", "限時", "優惠", "團購"]},
    {"name": "寵物",       "keywords": ["寵物", "貓咪", "狗狗", "毛孩", "寵物食品"]},
    {"name": "房地產",     "keywords": ["買房", "租屋", "預售屋", "房仲", "室內設計"]},
    {"name": "汽機車",     "keywords": ["汽車", "機車", "車險", "保養廠", "二手車"]},
    {"name": "婚禮婚慶",   "keywords": ["婚禮", "婚紗", "喜宴", "婚顧", "求婚"]},
    {"name": "家具家居",   "keywords": ["家具", "裝潢", "收納", "沙發", "床墊"]},
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
    # 初始化進度
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
    """
    取得下一個要執行的產業
    回傳 None 代表所有輪次已完成
    """
    current_round = progress["round"]
    if current_round > TOTAL_ROUNDS:
        return None  # 全部完成

    round_key = f"round{current_round}"
    industries = progress["industries"]

    # 從 current_industry_index 開始找還沒跑滿的產業
    idx = progress["current_industry_index"]
    for offset in range(len(industries)):
        i = (idx + offset) % len(industries)
        name = industries[i]
        stats = progress["industry_stats"].get(name, {})
        if stats.get(round_key, 0) < ADS_PER_INDUSTRY:
            progress["current_industry_index"] = i
            return next((ind for ind in INDUSTRY_LIST if ind["name"] == name), None)

    # 這一輪所有產業都跑滿了，進入下一輪
    if current_round < TOTAL_ROUNDS:
        progress["round"] = current_round + 1
        progress["current_industry_index"] = 0
        save_progress(progress)
        return get_next_industry(progress)  # 遞迴取下一輪的第一個產業

    return None  # 全部完成


def mark_industry_done(progress: dict, industry_name: str, count: int, new_ids: list):
    """記錄某產業本輪完成的數量"""
    current_round = progress["round"]
    round_key = f"round{current_round}"
    stats = progress["industry_stats"].setdefault(
        industry_name, {"round1": 0, "round2": 0, "processed_ids": []}
    )
    stats[round_key] = stats.get(round_key, 0) + count
    stats["processed_ids"] = list(set(stats.get("processed_ids", []) + new_ids))

    # 如果這個產業本輪已跑滿，移動到下一個
    if stats[round_key] >= ADS_PER_INDUSTRY:
        industries = progress["industries"]
        current_idx = progress["current_industry_index"]
        progress["current_industry_index"] = (current_idx + 1) % len(industries)

    save_progress(progress)


# ─── 無效廣告過濾 ─────────────────────────────────────────

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
            if days < 14:
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
    """呼叫 Meta Ad Library API"""
    if not META_ACCESS_TOKEN:
        return _get_mock_ads(keyword)

    url = "https://graph.facebook.com/v19.0/ads_archive"
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
        resp.raise_for_status()
        return resp.json().get("data", [])
    except requests.exceptions.RequestException as e:
        print(f"  ❌ Meta API 失敗（{keyword}）：{e}")
        return []


def _get_mock_ads(keyword: str) -> list:
    """模擬廣告資料（無 Token 時使用）"""
    base_days = [72, 55, 48, 38, 31, 22, 18]
    return [
        {
            "id": f"mock_{keyword}_{i:03d}",
            "page_name": f"台灣{keyword}品牌{chr(65+i)}",
            "ad_delivery_start_time": (datetime.now() - timedelta(days=base_days[i % len(base_days)])).isoformat(),
            "is_active": True,
            "ad_creative_bodies": [f"【{keyword}】第{i+1}支模擬廣告"],
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
    主函數：按產業輪流模式抓取高成效廣告
    每次執行選下一個未跑滿的產業，抓5支投放最久的廣告

    Returns:
        list of dict: [{"url", "ad_id", "page_name", "days_running",
                        "industry", "round", "is_mock"}]
    """
    progress = load_progress()

    # 取得本次要執行的產業
    industry = get_next_industry(progress)

    if industry is None:
        print("\n✅ 所有產業兩輪已完成！每個產業均有10支拆解數據。")
        print("   如需繼續，請手動重置 data/meta_industry_progress.json")
        return []

    current_round = progress["round"]
    industry_name = industry["name"]
    keywords = industry["keywords"]

    # 取得已處理過的 ID（去重用）
    stats = progress["industry_stats"].get(industry_name, {})
    processed_ids = set(stats.get("processed_ids", []))

    print(f"\n{'='*55}")
    print(f"Meta 廣告抓取 | 第 {current_round} 輪 | 產業：{industry_name}")
    print(f"目標：{ADS_PER_INDUSTRY} 支 | 已處理：{len(processed_ids)} 支（歷史去重）")
    print(f"{'='*55}")

    # 搜尋所有關鍵字，收集候選廣告
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
                        "page_name": ad.get("page_name", "未知粉專"),
                        "days_running": days,
                        "industry": industry_name,
                        "round": current_round,
                        "is_mock": ad.get("_mock", False),
                        "snapshot_url": ad.get("snapshot_url", ""),
                    })
        time.sleep(random.uniform(0.3, 0.8))

    # 去重（同一支廣告可能被多個關鍵字搜到）
    seen_ids = set()
    unique_candidates = []
    for c in candidates:
        if c["ad_id"] not in seen_ids:
            seen_ids.add(c["ad_id"])
            unique_candidates.append(c)

    # 按投放天數排序（最長壽的優先）
    unique_candidates.sort(key=lambda x: x["days_running"], reverse=True)

    # 取前 ADS_PER_INDUSTRY 支
    selected = unique_candidates[:ADS_PER_INDUSTRY]

    # 更新進度
    new_ids = [s["ad_id"] for s in selected]
    mark_industry_done(progress, industry_name, len(selected), new_ids)

    # 輸出結果
    print(f"\n篩選結果：{len(selected)} 支（共找到 {len(unique_candidates)} 支有效廣告）")
    for i, item in enumerate(selected, 1):
        mock_tag = "（模擬）" if item["is_mock"] else ""
        print(f"  {i}. {item['page_name']}{mock_tag}")
        print(f"     投放天數：{item['days_running']}天 | {item['url'][:50]}...")

    # 顯示整體進度
    _print_overall_progress(progress)

    return selected


def get_meta_ad_urls_simple() -> list:
    """簡化版：只回傳真實影片 URL 字串清單（供 daily_run.py 使用）"""
    items = get_meta_ad_urls_by_industry()
    return [item["url"] for item in items if not item["is_mock"]]


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
        f"Meta廣告進度：第{current_round}輪 {done}/{total} 個產業完成\n"
        f"每產業目標：{ADS_PER_INDUSTRY}支 × {TOTAL_ROUNDS}輪 = {ADS_PER_INDUSTRY * TOTAL_ROUNDS}支"
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
    print("測試 Meta 廣告素材抓取器（按產業輪流模式）...")
    results = get_meta_ad_urls_by_industry()
    print(f"\n本次抓取：{len(results)} 支廣告")
    print(f"\n{get_progress_summary()}")
