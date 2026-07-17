"""
爆款短影音拆解工廠 - 每日排程主執行腳本 v3.0
好創整合行銷 | 子權 2026-06-11

執行方式：python3 daily_run.py（於專案目錄下執行，路徑自動偵測）
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
import uuid
from datetime import datetime
from pathlib import Path

# 動態計算路徑，避免硬編碼 /home/ubuntu 導致環境移植失敗
BASE_DIR = Path(__file__).resolve().parent

# 確保可以 import 同目錄的模組
sys.path.insert(0, str(BASE_DIR))

from trending_fetcher import acknowledge_manual_queue, get_daily_urls
from viral_factory import process_batch, send_batch_reports, send_slack_dm
from weekly_report import run_weekly_report
from meta_ads_fetcher import get_meta_ad_urls_simple
from script_distributor import distribute_video
from manual_version_tracker import auto_detect_and_update

# ─── 日誌設定 ─────────────────────────────────────────────
LOG_DIR = BASE_DIR / "logs"
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

def _check_env() -> bool:
    """啟動前驗證必要環境變數，避免執行到一半才報錯"""
    required = {}
    optional = {
        # 轉錄已改用 manus-speech-to-text、GPT 走沙盒代理，OPENAI_API_KEY 已非必要
        "OPENAI_API_KEY": "備用：直接呼叫 OpenAI API 時使用（目前主流程不需要）",
        "META_ACCESS_TOKEN": "用於 Meta 廣告庫拆取（缺少則僅有機內容）",
        "WHISPER_AVAILABLE": "控制是否啟用轉錄（預設 true）",
        "SLACK_BOT_TOKEN": "用於發送 Slack DM 與頻道通知（缺少則所有通知都會失敗）",
    }
    all_ok = True
    for key, desc in required.items():
        val = os.environ.get(key, "")
        if not val:
            logger.error(f"❌ 缺少必要環境變數 {key}  用途：{desc}")
            all_ok = False
        else:
            logger.info(f"✅ {key} 已設定（前8碼：{val[:8]}...)「{desc}」")
    for key, desc in optional.items():
        val = os.environ.get(key, "")
        if not val:
            logger.warning(f"⚠️  {key} 未設定（非必要）  用途：{desc}")
    return all_ok


def main():
    start_time = datetime.now()
    run_id = f"run-{datetime.now().astimezone().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    logger.info("=" * 60)
    logger.info(f"爆款短影音拆解工廠 v3.0 啟動 | {start_time.strftime('%Y-%m-%d %H:%M')}")
    logger.info(f"run_id={run_id}")
    logger.info("雙來源系統：TikTok/Reels 有機內容 + Meta 廣告素材")
    logger.info("新增：評分系統 + 素材自動分配（03/04/05/06/07/35 庫）")
    logger.info("=" * 60)

    # 環境變數驗證（必要 Key 缺少則中止）
    if not _check_env():
        logger.error("必要環境變數缺少，任務中止。請檢查 .env 檔案。")
        raise RuntimeError("必要環境變數缺少，任務中止")

    # 判斷 Whisper 是否可用（OpenAI 額度）
    WHISPER_AVAILABLE = os.environ.get("WHISPER_AVAILABLE", "true").lower() == "true"
    if not WHISPER_AVAILABLE:
        logger.warning("Whisper 已停用（API 額度不足），逐字稿欄位將顯示「待補充」")

    try:
        # Step 1-A：取得有機短影音 URL（TikTok/Reels，目標 5 支）
        logger.info("\n[Step 1-A] 抓取今日熱門有機短影音（TikTok/Reels）...")
        organic_urls = get_daily_urls()
        logger.info(f"有機內容：{len(organic_urls)} 支")

        # Step 1-B：取得 Meta 廣告素材（按產業輪流，英國市場）
        logger.info("\n[Step 1-B] 抓取 Meta 廣告高成效素材（英國市場，按產業輪流）...")
        meta_items = []   # list of dict: {url, country_label, source_note, ...}
        try:
            meta_items = get_meta_ad_urls_simple()  # 回傳字典清單
            meta_urls = [item["url"] for item in meta_items if not item.get("is_mock", False)]
            meta_mock_count = sum(1 for item in meta_items if item.get("is_mock", False))
            logger.info(f"Meta 廣告：{len(meta_urls)} 支真實 + {meta_mock_count} 支模擬（已略過）")
            if meta_urls:
                logger.info(f"  標註：🌍 國外廣告（英國）")
        except Exception as e:
            logger.warning(f"Meta 廣告抓取失敗（不影響有機內容）：{e}")
            meta_urls = []

        # 合併雙來源（有機內容優先，Meta 廣告附後）
        urls = organic_urls + meta_urls

        if not urls:
            logger.warning("今日無待拆影片；仍發送零輸入健康狀態，不得靜默結束")
            send_batch_reports([], run_id)
            return

        logger.info(
            f"\n今日待拆合計：{len(urls)} 支"
            f"（有機 {len(organic_urls)} + Meta廣告英國 {len(meta_urls)}）"
        )

        # Step 2：批次拆解（雙來源合併處理）
        logger.info("\n[Step 2] 開始批次拆解（雙來源）...")
        results = process_batch(urls, whisper_available=WHISPER_AVAILABLE, run_id=run_id)
        removed_manual = acknowledge_manual_queue(results)
        logger.info(f"手動待拆清單已確認移除 {removed_manual} 筆終態項目；失敗／隔離項目保留待重試")

        # Step 3：統計拆解結果
        success_results = [r for r in results if r.get("success")]
        failed_results  = [r for r in results if not r.get("success")]

        logger.info(f"\n拆解完成：成功 {len(success_results)} 支 | 失敗 {len(failed_results)} 支")

        # Step 4 & 5 & 6：評分 + 素材分配（每支成功拆解的影片）
        logger.info("\n[Step 4-6] 開始評分與素材分配...")

        score_summary = {"5分": 0, "4分": 0, "3分": 0, "2分": 0, "1分": 0, "未評分": 0}
        type_summary = {"IP型": 0, "導購型": 0}
        high_score_count = 0

        for result in success_results:
            if not result.get("analysis"):
                logger.warning(f"[跳過] 無拆解結果：{result.get('url', '')[:50]}")
                continue

            analysis = result["analysis"]
            source_url = result.get("url", "")

            try:
                dist_result = distribute_video(analysis, source_url)

                # distribute_video 新版回傳 {score_result, library_urls}
                if isinstance(dist_result, dict) and "score_result" in dist_result:
                    score_result = dist_result["score_result"]
                    library_urls = dist_result.get("library_urls", {})
                else:
                    # 舊版相容（直接回傳 score_result）
                    score_result = dist_result
                    library_urls = {}

                # 把庫房連結寫回 result，讓 process_batch 的日報可以用
                result["library_urls"] = library_urls
                result["score"] = score_result.get("score")
                result["score_label"] = score_result.get("score_label", "未評分")
                result["score_status"] = score_result.get("score_status", "unknown")
                result["distribution_status"] = dist_result.get("distribution_status", "unknown") if isinstance(dist_result, dict) else "unknown"
                result["distribution_failures"] = dist_result.get("distribution_failures", []) if isinstance(dist_result, dict) else []

                # 統計評分
                score = score_result.get("score")
                content_type = score_result.get("content_type")

                if score == 5:
                    score_summary["5分"] += 1
                elif score == 4:
                    score_summary["4分"] += 1
                elif score == 3:
                    score_summary["3分"] += 1
                elif score == 2:
                    score_summary["2分"] += 1
                elif score == 1:
                    score_summary["1分"] += 1
                else:
                    score_summary["未評分"] += 1

                if content_type in type_summary:
                    type_summary[content_type] += 1

                if any(key.startswith("35｜") for key in library_urls):
                    high_score_count += 1

                lib_summary = ' | '.join([f"{k}" for k in library_urls.keys()]) if library_urls else "無"
                log_method = logger.info if result["distribution_status"] == "completed" else logger.error
                log_method(
                    f"[{'✓' if result['distribution_status'] == 'completed' else '✗'}] 分配{result['distribution_status']}：{analysis.get('影片標題或主題', '')[:30]} | "
                    f"{score_result.get('score_label')} | {content_type} | "
                    f"素材庫：{lib_summary}"
                )

            except Exception as e:
                logger.error(f"[✗] 素材分配失敗：{source_url[:50]} | {e}", exc_info=True)
                # 02 主庫已經 read-back 成功，不能倒寫成「入庫失敗」。
                # 下游分配以獨立狀態呈現，讓日報可同時說清楚兩件事。
                result["distribution_status"] = "technical_error"
                result["distribution_error"] = str(e)

        # Step 7：最終統計日報
        final_success_results = [r for r in results if r.get("success")]
        final_non_success = [r for r in results if not r.get("success")]
        elapsed = (datetime.now() - start_time).seconds
        logger.info(f"\n{'='*60}")
        logger.info(f"任務完成 | 耗時 {elapsed}s")
        logger.info(f"唯一有效成功：{len(final_success_results)} 支 | 非成功：{len(final_non_success)} 支")
        logger.info(f"  有機內容：{sum(1 for r in final_success_results if r.get('url','') in organic_urls)} 支")
        logger.info(f"  Meta廣告（英國）：{sum(1 for r in final_success_results if r.get('url','') in meta_urls)} 支")
        logger.info(f"評分分佈：{score_summary}")
        logger.info(f"類型分佈：{type_summary}")
        logger.info(f"高分入庫（35庫）：{high_score_count} 支")
        logger.info(f"{'='*60}")

        logger.info("\n[Step 7] 評分與分配完成後才發送日報...")
        report_status = send_batch_reports(results, run_id)
        if not report_status["daily"].get("success"):
            logger.error(f"日報發送失敗：{report_status['daily'].get('error')}")
        if not report_status["health"].get("success"):
            logger.error(f"健康摘要發送失敗：{report_status['health'].get('error')}")

        # 週五額外執行週報分析
        if (
            datetime.now().weekday() == 4
            and os.environ.get("RUN_WEEKLY_INSIDE_DAILY", "false").lower() == "true"
        ):
            logger.info("\n[週五任務] 開始執行爆款規律週報分析...")
            weekly_result = run_weekly_report()
            if weekly_result.get("success"):
                logger.info(f"週報完成：{weekly_result.get('status')}")
            else:
                logger.error(f"週報被資料閘門阻擋：{weekly_result.get('error')}")
        elif datetime.now().weekday() == 4:
            logger.info("週報內嵌排程已停用；僅允許一個獨立週報排程")

        # ─── 手冊版本自動更新 ─────────────────────────────────
        # 每次拆解完成後自動偵測：
        #   - 若腳本有變動 → bump minor（功能更新）並發 Slack 通知
        #   - 若只有資料更新 → bump patch（靜默更新，不發通知）
        logger.info("\n[手冊更新] 自動偵測版本變動...")
        try:
            if os.environ.get("AUTO_UPDATE_MANUAL", "false").lower() != "true":
                logger.info("手冊自動改寫已停用；正式 SOP 必須經人審後更新")
            else:
                patch_changes = [
                    f"今日唯一有效新增：{len(final_success_results)} 支（有機 + Meta廣告）",
                    f"評分分佈：{score_summary}",
                    f"高分入庫（35庫）：{high_score_count} 支"
                ]
                new_version = auto_detect_and_update(
                    reason="經核准的爆款系統資料更新",
                    changes=patch_changes,
                    notify_slack=True
                )
                logger.info(f"手冊已更新至 v{new_version}")
        except Exception as e:
            logger.warning(f"手冊版本更新失敗（不影響主流程）：{e}")
        # ─────────────────────────────────────────────────────

    except Exception as e:
        logger.error(f"排程執行失敗：{e}", exc_info=True)
        try:
            alert = (
                f"⛔ *爆款資料管線排程失敗*\n"
                f"run_id：`{run_id}`\n"
                f"error：{type(e).__name__}: {str(e)[:500]}\n"
                "本訊息只代表系統錯誤，沒有任何入庫成功推定。"
            )
            send_result = send_slack_dm(
                alert,
                channel=os.environ.get("SLACK_DENNIS_ID", "U0ARRQS3XPS"),
            )
            if not send_result.get("success"):
                logger.error(f"排程失敗警示亦發送失敗：{send_result.get('error')}")
        except Exception as alert_exc:
            logger.error(f"排程失敗警示異常：{alert_exc}", exc_info=True)
        raise


def _send_management_report():
    """發送管理日報給子權（無論主流程成功或失敗都執行）"""
    print("\n" + "="*50)
    print(f"📊 正在生成管理日報並發送至 Slack...")
    try:
        from ack_collector import collect_acknowledgements
        from management_report import generate_management_report
        from viral_factory import send_slack_dm

        ack_summary = collect_acknowledgements()
        print(
            "  ACK collector："
            f"tracked={ack_summary['tracked']} | confirmed={ack_summary['confirmed']} | "
            f"no_ack={ack_summary['no_ack']} | errors={len(ack_summary['errors'])}"
        )
        mgt_report = generate_management_report()
        # 發送到主頻道（從環境變數讀取，不硬編碼）
        send_result = send_slack_dm(mgt_report, channel=os.environ.get("SLACK_TEAM_CH", "C0AQG307XJT"))
        if send_result.get("success"):
            print(f"  ✅ 管理日報已送出（{send_result.get('status')}）")
        else:
            print(f"  ❌ 管理日報發送失敗：{send_result.get('error')}")
    except Exception as e:
        print(f"  ⚠️ 管理日報發送失敗: {e}")
    print("="*50)


if __name__ == "__main__":
    exit_code = 0
    try:
        main()
    except Exception as e:
        # 不直接 re-raise：先確保管理日報一定送出，再以非零碼結束
        logger.error(f"每日排程失敗：{e}", exc_info=True)
        exit_code = 1
    finally:
        # P0：管理日報不可在通知後立即把送出誤稱為確認。
        # 待獨立 ACK collector 於合理視窗後執行時再開啟。
        if os.environ.get("SEND_MANAGEMENT_REPORT_IMMEDIATELY", "false").lower() == "true":
            _send_management_report()
        else:
            logger.info("即時管理日報已停用；等待獨立確認收集流程")

    sys.exit(exit_code)
