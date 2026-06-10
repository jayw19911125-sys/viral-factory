"""
Meta 廣告素材抓取器 v1.0
好創整合行銷 | 子權 2026-06-10

功能：從 Meta Ad Library（廣告資料庫）抓取競品/同業的高成效廣告素材
     重點抓取「投放超過30天」的長壽廣告（長壽 = 高成效指標）

流程：
1. 呼叫 Meta Ad Library API 搜尋指定關鍵字/粉專的廣告
2. 過濾出「高成效廣告特徵」的素材
3. 回傳影片 URL 清單，供 viral_factory.py 拆解

無效廣告過濾標準（自動排除）：
- 投放天數 < 14 天（太新，無法判斷成效）
- 無影片素材（純圖片廣告，跳過）
- 廣告已停播（active = false）

高成效廣告特徵（優先選取）：
- 投放天數 > 30 天（長壽廣告）
- 使用影片格式（非靜態圖片）
- 有明確的 CTA 按鈕
"""

import os
import json
import time
import random
import requests
from datetime import datetime, timedelta
from pathlib import Path

# ─── 設定區 ───────────────────────────────────────────────
# Meta Ad Library API 設定
# 需要 Meta Business 帳號的 Access Token（免費申請）
# 申請方式：https://www.facebook.com/ads/library/api/
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "")

# 搜尋設定
SEARCH_COUNTRY = "TW"          # 台灣市場
SEARCH_AD_TYPE = "ALL"         # 所有廣告類型
MAX_ADS_PER_QUERY = 10         # 每次查詢最多抓幾支
DAILY_META_TARGET = 2          # 每日目標：2支 Meta 廣告

# 資料儲存路徑
DATA_DIR = Path("/home/ubuntu/viral_factory/data")
META_CACHE_FILE = DATA_DIR / "meta_ads_cache.json"

# 預設搜尋關鍵字（台灣市場高競爭產業）
DEFAULT_SEARCH_TERMS = [
    "美容",
    "保養",
    "餐廳",
    "電商",
    "健身",
    "課程",
    "服飾",
]

# ─── 無效廣告過濾邏輯 ─────────────────────────────────────

def is_valid_ad(ad: dict) -> bool:
    """
    判斷廣告是否值得拆解
    回傳 True = 有效，False = 無效跳過
    """
    # 必須是投放中的廣告
    if not ad.get("is_active", False):
        return False

    # 必須有影片素材
    creatives = ad.get("ad_creative_bodies", []) or []
    media = ad.get("ad_creative_link_captions", []) or []
    videos = ad.get("videos", []) or []

    # 檢查是否有影片
    has_video = bool(videos) or any(
        "video" in str(c).lower() for c in creatives
    )
    if not has_video:
        return False

    # 計算投放天數
    start_date_str = ad.get("ad_delivery_start_time", "")
    if start_date_str:
        try:
            start_date = datetime.fromisoformat(start_date_str.replace("Z", "+00:00"))
            days_running = (datetime.now(start_date.tzinfo) - start_date).days
            if days_running < 14:
                return False  # 太新，無法判斷成效
        except Exception:
            pass

    return True


def score_ad(ad: dict) -> float:
    """
    計算廣告的「成效潛力分數」，用於排序
    分數越高 = 越值得拆解
    """
    score = 0.0

    # 投放天數（最重要指標）
    start_date_str = ad.get("ad_delivery_start_time", "")
    if start_date_str:
        try:
            start_date = datetime.fromisoformat(start_date_str.replace("Z", "+00:00"))
            days_running = (datetime.now(start_date.tzinfo) - start_date).days
            score += min(days_running / 30, 3.0)  # 最高加3分（90天以上）
        except Exception:
            pass

    # 有 CTA 按鈕
    if ad.get("ad_creative_link_captions"):
        score += 0.5

    # 有多個廣告版本（A/B 測試中的廣告）
    if len(ad.get("ad_creative_bodies", [])) > 1:
        score += 0.5

    # 有目標網址（導購型廣告）
    if ad.get("ad_creative_link_urls"):
        score += 1.0

    return score


# ─── Meta Ad Library API 呼叫 ─────────────────────────────

def search_meta_ads(search_term: str, limit: int = 10) -> list:
    """
    呼叫 Meta Ad Library API 搜尋廣告
    API 文件：https://www.facebook.com/ads/library/api/
    """
    if not META_ACCESS_TOKEN:
        print("  ⚠️  META_ACCESS_TOKEN 未設定，使用模擬資料")
        return _get_mock_ads(search_term)

    url = "https://graph.facebook.com/v19.0/ads_archive"
    params = {
        "access_token": META_ACCESS_TOKEN,
        "ad_type": SEARCH_AD_TYPE,
        "ad_reached_countries": f'["{SEARCH_COUNTRY}"]',
        "search_terms": search_term,
        "fields": ",".join([
            "id",
            "ad_creative_bodies",
            "ad_creative_link_captions",
            "ad_creative_link_descriptions",
            "ad_creative_link_titles",
            "ad_creative_link_urls",
            "ad_delivery_start_time",
            "ad_delivery_stop_time",
            "is_active",
            "page_name",
            "page_id",
            "videos",
            "snapshot_url",
        ]),
        "limit": limit,
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("data", [])
    except requests.exceptions.RequestException as e:
        print(f"  ❌ Meta API 呼叫失敗：{e}")
        return []


def _get_mock_ads(search_term: str) -> list:
    """
    當 META_ACCESS_TOKEN 未設定時，回傳模擬廣告資料
    用於測試流程是否正常
    """
    mock_ads = [
        {
            "id": f"mock_{search_term}_001",
            "page_name": f"台灣{search_term}品牌A",
            "ad_delivery_start_time": (datetime.now() - timedelta(days=45)).isoformat(),
            "is_active": True,
            "ad_creative_bodies": [f"【限時優惠】{search_term}產品，買一送一！"],
            "ad_creative_link_urls": ["https://example.com"],
            "videos": [{"video_url": "MOCK_VIDEO_URL_001"}],
            "snapshot_url": "https://www.facebook.com/ads/library/",
            "_mock": True,
            "_search_term": search_term,
        },
        {
            "id": f"mock_{search_term}_002",
            "page_name": f"台灣{search_term}品牌B",
            "ad_delivery_start_time": (datetime.now() - timedelta(days=62)).isoformat(),
            "is_active": True,
            "ad_creative_bodies": [f"你還在為{search_term}煩惱嗎？試試這個方法！"],
            "ad_creative_link_urls": ["https://example.com"],
            "videos": [{"video_url": "MOCK_VIDEO_URL_002"}],
            "snapshot_url": "https://www.facebook.com/ads/library/",
            "_mock": True,
            "_search_term": search_term,
        },
    ]
    return mock_ads


def extract_video_url(ad: dict) -> str:
    """從廣告資料中提取影片 URL"""
    videos = ad.get("videos", [])
    if videos and isinstance(videos, list):
        for v in videos:
            if isinstance(v, dict):
                url = v.get("video_url") or v.get("url") or ""
                if url and url != "MOCK_VIDEO_URL_001" and url != "MOCK_VIDEO_URL_002":
                    return url
    # 如果是模擬資料，回傳 snapshot_url 供人工查看
    return ad.get("snapshot_url", "")


def load_meta_cache() -> set:
    """載入已處理過的廣告 ID 快取"""
    if META_CACHE_FILE.exists():
        try:
            data = json.loads(META_CACHE_FILE.read_text(encoding="utf-8"))
            return set(data.get("processed_ids", []))
        except Exception:
            pass
    return set()


def save_meta_cache(processed_ids: set):
    """儲存已處理過的廣告 ID"""
    DATA_DIR.mkdir(exist_ok=True)
    data = {
        "processed_ids": list(processed_ids),
        "updated_at": datetime.now().isoformat()
    }
    META_CACHE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# ─── 主函數 ───────────────────────────────────────────────

def get_meta_ad_urls(
    search_terms: list = None,
    target_count: int = DAILY_META_TARGET
) -> list:
    """
    主函數：取得今日待拆解的 Meta 廣告 URL 清單

    Args:
        search_terms: 搜尋關鍵字清單，預設使用 DEFAULT_SEARCH_TERMS
        target_count: 目標抓取數量

    Returns:
        list of dict: [{"url": str, "ad_id": str, "page_name": str, "days_running": int, "is_mock": bool}]
    """
    if search_terms is None:
        search_terms = DEFAULT_SEARCH_TERMS

    print(f"\n{'='*55}")
    print(f"Meta 廣告素材抓取器 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"搜尋關鍵字：{', '.join(search_terms[:3])}...")
    print(f"目標：{target_count} 支高成效廣告")
    print(f"{'='*55}")

    processed_ids = load_meta_cache()
    candidates = []

    # 隨機選取幾個關鍵字搜尋（避免每次都搜同樣的）
    selected_terms = random.sample(search_terms, min(3, len(search_terms)))

    for term in selected_terms:
        print(f"\n  搜尋「{term}」...")
        ads = search_meta_ads(term, limit=MAX_ADS_PER_QUERY)
        print(f"  取得 {len(ads)} 筆廣告")

        for ad in ads:
            ad_id = ad.get("id", "")

            # 去重
            if ad_id in processed_ids:
                continue

            # 過濾無效廣告
            if not is_valid_ad(ad):
                continue

            # 計算分數
            score = score_ad(ad)
            video_url = extract_video_url(ad)

            if not video_url:
                continue

            # 計算投放天數
            days_running = 0
            start_date_str = ad.get("ad_delivery_start_time", "")
            if start_date_str:
                try:
                    start_date = datetime.fromisoformat(start_date_str.replace("Z", "+00:00"))
                    days_running = (datetime.now(start_date.tzinfo) - start_date).days
                except Exception:
                    pass

            candidates.append({
                "url": video_url,
                "ad_id": ad_id,
                "page_name": ad.get("page_name", "未知粉專"),
                "days_running": days_running,
                "score": score,
                "is_mock": ad.get("_mock", False),
                "search_term": ad.get("_search_term", term),
                "snapshot_url": ad.get("snapshot_url", ""),
            })

        time.sleep(random.uniform(0.5, 1.5))

    # 按分數排序
    candidates.sort(key=lambda x: x["score"], reverse=True)

    # 取前 N 支
    selected = candidates[:target_count]

    # 更新快取
    for item in selected:
        processed_ids.add(item["ad_id"])
    save_meta_cache(processed_ids)

    # 輸出結果
    print(f"\n{'='*55}")
    print(f"篩選結果：{len(selected)} 支高成效廣告")
    for i, item in enumerate(selected, 1):
        mock_tag = "（模擬）" if item["is_mock"] else ""
        print(f"  {i}. {item['page_name']}{mock_tag}")
        print(f"     投放天數：{item['days_running']}天 | 分數：{item['score']:.1f}")
        print(f"     URL：{item['url'][:60]}...")
    print(f"{'='*55}\n")

    return selected


def get_meta_ad_urls_simple() -> list:
    """
    簡化版：只回傳 URL 字串清單（供 daily_run.py 使用）
    """
    items = get_meta_ad_urls()
    return [item["url"] for item in items if not item["is_mock"]]


# ─── 執行入口 ─────────────────────────────────────────────

if __name__ == "__main__":
    print("測試 Meta 廣告素材抓取器...")
    results = get_meta_ad_urls()
    print(f"\n最終結果：{len(results)} 支廣告")
    for r in results:
        print(f"  - {r['page_name']} | {r['days_running']}天 | {r['url'][:50]}")
