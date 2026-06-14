"""
爆款短影音拆解工廠 - 每日排程主執行腳本 v3.0
好創整合行銷 | 子權 2026-06-11

執行方式：python3 /home/ubuntu/viral_factory/daily_run.py
排程：每日 09:00（週一至週五）

流程（雙來源系統）：
1-A. 從熱門榜單抓取今日 5 支有機短影音（TikTok/Reels）
1-B. 從 Meta Ad Library 按產業輪流抓取高成效廣告（每產業5支/輪）
2.   合併後逐支執行完整拆解流程
3.   寫入 Notion 02｜爆款拆解庫（完整12欄位+6標籤）
4.   評分（1-5分）+ 判斷 IP型/導購型
5.   素材無遺漏分配到 03/04/05/06/07 各庫
6.   高分（4-5分）寫入 35｜已驗證熱門腳本庫
7.   發送 Slack 日報

拆解框架：v4.0 頂尖方法論版
- 視覺錘 × 語言釘分析
- 五大鉤子類型識別
- 人設定位分析
- 廣告投放潛力評估
- 產業適用性分析
- 神經科學機制分析
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
from script_distributor import distribute_video
from manual_version_tracker import auto_detect_and_update

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
    logger.info(f"爆款短影音拆解工廠 v3.0 啟動 | {start_time.strftime('%Y-%m-%d %H:%M')}")
    logger.info("雙來源系統：TikTok/Reels 有機內容 + Meta 廣告素材")
    logger.info("新增：評分系統 + 素材自動分配（03/04/05/06/07/35 庫）")
    logger.info("=" * 60)

    # 判斷 Whisper 是否可用（OpenAI 額度）
    WHISPER_AVAILABLE = os.environ.get("WHISPER_AVAILABLE", "true").lower() == "true"
    if not WHISPER_AVAILABLE:
        logger.warning("Whisper 已停用（API 額度不足），逐字稿欄位將顯示「待補充」")

    try:
        # Step 1-A：取得有機短影音 URL（TikTok/Reels，目標 5 支）
        logger.info("\n[Step 1-A] 抓取今日熱門有機短影音（TikTok/Reels）...")
        organic_urls = get_daily_urls()
        logger.info(f"有機內容：{len(organic_urls)} 支")

        # Step 1-B：取得 Meta 廣告素材 URL（按產業輪流）
        logger.info("\n[Step 1-B] 抓取 Meta 廣告高成效素材（按產業輪流）...")
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

        # Step 3：統計拆解結果
        success_results = [r for r in results if r.get("success")]
        failed_results  = [r for r in results if not r.get("success")]

        logger.info(f"\n拆解完成：成功 {len(success_results)} 支 | 失敗 {len(failed_results)} 支")

        # Step 4 & 5 & 6：評分 + 素材分配（每支成功拆解的影片）
        logger.info("\n[Step 4-6] 開始評分與素材分配...")

        score_summary = {"5分": 0, "4分": 0, "3分": 0, "2分": 0, "1分": 0}
        type_summary = {"IP型": 0, "導購型": 0}
        high_score_count = 0

        for result in success_results:
            if not result.get("analysis"):
                logger.warning(f"[跳過] 無拆解結果：{result.get('url', '')[:50]}")
                continue

            analysis = result["analysis"]
            source_url = result.get("url", "")

            try:
                score_result = distribute_video(analysis, source_url)

                # 統計評分
                score = score_result.get("score", 0)
                content_type = score_result.get("content_type", "IP型")

                if score == 5:
                    score_summary["5分"] += 1
                elif score == 4:
                    score_summary["4分"] += 1
                elif score == 3:
                    score_summary["3分"] += 1
                elif score == 2:
                    score_summary["2分"] += 1
                else:
                    score_summary["1分"] += 1

                if content_type in type_summary:
                    type_summary[content_type] += 1

                if score >= 4:
                    high_score_count += 1

                logger.info(
                    f"[✓] 分配完成：{analysis.get('title', '')[:30]} | "
                    f"{score_result.get('score_label')} | {content_type}"
                )

            except Exception as e:
                logger.error(f"[✗] 素材分配失敗：{source_url[:50]} | {e}", exc_info=True)

        # Step 7：最終統計日報
        elapsed = (datetime.now() - start_time).seconds
        logger.info(f"\n{'='*60}")
        logger.info(f"任務完成 | 耗時 {elapsed}s")
        logger.info(f"拆解：成功 {len(success_results)} 支 | 失敗/跳過 {len(failed_results)} 支")
        logger.info(f"  有機內容：{sum(1 for r in success_results if r.get('url','') in organic_urls)} 支")
        logger.info(f"  Meta廣告：{sum(1 for r in success_results if r.get('url','') in meta_urls)} 支")
        logger.info(f"評分分佈：{score_summary}")
        logger.info(f"類型分佈：{type_summary}")
        logger.info(f"高分入庫（35庫）：{high_score_count} 支")
        logger.info(f"{'='*60}")

        # 週五額外執行週報分析
        if datetime.now().weekday() == 4:  # 4 = 週五
            logger.info("\n[週五任務] 開始執行爆款規律週報分析...")
            run_weekly_report()
            logger.info("週報分析完成，已發送至 Slack 團隊主頻道")

        # ─── 手冊版本自動更新 ─────────────────────────────────
        # 每次拆解完成後自動偵測：
        #   - 若腳本有變動 → bump minor（功能更新）並發 Slack 通知
        #   - 若只有資料更新 → bump patch（靜默更新，不發通知）
        logger.info("\n[手冊更新] 自動偵測版本變動...")
        try:
            patch_changes = [
                f"今日拆解：成功 {len(success_results)} 支（有機 + Meta廣告）",
                f"評分分佈：{score_summary}",
                f"高分入庫（35庫）：{high_score_count} 支"
            ]
            new_version = auto_detect_and_update(
                reason="每日拆解資料更新",
                changes=patch_changes,
                notify_slack=True  # minor 版才會發通知，patch 靜默
            )
            logger.info(f"手冊已更新至 v{new_version}")
        except Exception as e:
            logger.warning(f"手冊版本更新失敗（不影響主流程）：{e}")
        # ─────────────────────────────────────────────────────

    except Exception as e:
        logger.error(f"排程執行失敗：{e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
