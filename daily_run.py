"""
爆款短影音拆解工廠 - 每日排程主執行腳本 v2.0
好創整合行銷 | 子權 2026-06-10

執行方式：python3 /home/ubuntu/viral_factory/daily_run.py
排程：每日 09:00（週一至週五）

流程（雙來源系統）：
1-A. 從熱門榜單抓取今日 4 支有機短影音（TikTok/Reels）
1-B. 從 Meta Ad Library 抓取今日 2 支高成效廣告素材
2.   合併後逐支執行完整拆解流程（共 6 支）
3.   寫入 Notion 爆款拆解庫
4.   發送 Slack 日報給阿韋

拆解框架：v3.0 頂尖方法論版
- 視覺錘 × 語言釘分析
- 五大鉤子類型識別
- 人設定位分析
- 廣告投放潛力評估
- 產業適用性分析
- 無效因素識別
"""

import sys
import os
import logging
from datetime import datetime
from pathlib import Path

# 確保可以 import 同目錄的模組
sys.path.insert(0, str(Path(__file__).parent))

from trending_fetcher import get_daily_urls
from viral_factory import process_batch
from weekly_report import run_weekly_report
from meta_ads_fetcher import get_meta_ad_urls_simple

# ─── 日誌設定 ─────────────────────────────────────────────
LOG_DIR = Path("/home/ubuntu/viral_factory/logs")
LOG_DIR.mkdir(exist_ok=True)

today = datetime.now().strftime("%Y%m%d")
log_file = LOG_DIR / f"{today}_daily_run.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(str(log_file), encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


# ─── 主執行 ───────────────────────────────────────────────

def main():
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info(f"爆款短影音拆解工廠 v2.0 啟動 | {start_time.strftime('%Y-%m-%d %H:%M')}")
    logger.info("雙來源系統：TikTok/Reels 有機內容 + Meta 廣告素材")
    logger.info("=" * 60)

    # 判斷 Whisper 是否可用（OpenAI 額度）
    # 若額度不足，設為 false，系統會跳過轉錄，其他功能正常運作
    WHISPER_AVAILABLE = os.environ.get("WHISPER_AVAILABLE", "true").lower() == "true"
    if not WHISPER_AVAILABLE:
        logger.warning("Whisper 已停用（API 額度不足），逐字稿欄位將顯示「待補充」")

    try:
        # Step 1-A：取得有機短影音 URL（TikTok/Reels，目標 4 支）
        logger.info("\n[Step 1-A] 抓取今日熱門有機短影音（TikTok/Reels）...")
        organic_urls = get_daily_urls()
        logger.info(f"有機內容：{len(organic_urls)} 支")

        # Step 1-B：取得 Meta 廣告素材 URL（目標 2 支）
        logger.info("\n[Step 1-B] 抓取 Meta 廣告高成效素材...")
        try:
            meta_urls = get_meta_ad_urls_simple()
            logger.info(f"Meta 廣告：{len(meta_urls)} 支")
        except Exception as e:
            logger.warning(f"Meta 廣告抓取失敗（不影響有機內容）：{e}")
            meta_urls = []

        # 合併雙來源（有機內容優先，Meta 廣告附後）
        urls = organic_urls + meta_urls

        if not urls:
            logger.warning("今日無待拆影片，任務結束")
            return

        logger.info(
            f"\n今日待拆合計：{len(urls)} 支"
            f"（有機 {len(organic_urls)} + Meta廣告 {len(meta_urls)}）"
        )

        # Step 2：批次拆解（雙來源合併處理）
        logger.info("\n[Step 2] 開始批次拆解（雙來源）...")
        results = process_batch(urls, whisper_available=WHISPER_AVAILABLE)

        # Step 3：統計結果
        success = [r for r in results if r.get("success")]
        failed  = [r for r in results if not r.get("success")]

        elapsed = (datetime.now() - start_time).seconds
        logger.info(f"\n{'='*60}")
        logger.info(f"任務完成 | 耗時 {elapsed}s")
        logger.info(f"成功：{len(success)} 支 | 失敗/跳過：{len(failed)} 支")
        logger.info(f"  有機內容成功：{sum(1 for r in success if r.get('url','') in organic_urls)} 支")
        logger.info(f"  Meta廣告成功：{sum(1 for r in success if r.get('url','') in meta_urls)} 支")
        logger.info(f"{'='*60}")

        # 週五額外執行週報分析（每週五 09:00 拆解完成後自動觸發）
        if datetime.now().weekday() == 4:  # 4 = 週五
            logger.info("\n[週五任務] 開始執行爆款規律週報分析...")
            run_weekly_report()
            logger.info("週報分析完成，已發送至 Slack 團隊主頻道")

    except Exception as e:
        logger.error(f"排程執行失敗：{e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
