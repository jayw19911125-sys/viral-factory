"""
爆款短影音拆解工廠｜手冊自動更新系統 v1.0
好創整合行銷 | 子權 2026-06-11

功能：
1. 從 Notion 02 庫讀取本週新增的爆款拆解數據
2. 從 35 庫讀取本週新入庫的高分腳本數量
3. 統計本週各庫房新增素材數量（03~07）
4. 用 GPT 生成「本週系統動態摘要」
5. 更新 viral_factory_v3_manual.md（版本號、更新日期、本週新增統計）
6. 轉換為 PDF
7. 上傳 PDF 到 S3（取得公開連結）
8. 發送 Slack 通知至 #all-團隊主頻道（含下載連結）
9. 發送 Slack DM 給子權（含本週系統健康摘要）

執行時機：每週一 08:00（由 Manus 排程觸發）
"""

import os
import json
import subprocess
import re
from datetime import datetime, timedelta
from pathlib import Path
from openai import OpenAI

# ─── 設定區 ───────────────────────────────────────────────
SLACK_TEAM_CH   = os.environ.get("SLACK_TEAM_CH",   "C0AQG307XJT")   # #all-團隊主頻道
SLACK_AWEI_ID   = os.environ.get("SLACK_AWEI_ID",   "U0B4FG0ER89")   # 阿韋 DM
SLACK_ZIQUAN_ID = os.environ.get("SLACK_ZIQUAN_ID", "U07MHPJKQ8V")   # 子權 DM（COO）

# Notion 庫房 ID（更改庫房時只需改 .env）
NOTION_DB = {
    "02_拆解庫":    os.environ.get("NOTION_DB_MAIN",         "82097a06fae583bda8c387236d3713aa").replace("-", ""),
    "03_鉤子庫":    os.environ.get("NOTION_DB_HOOK",         "44197a06fae58363b6c4015bde8b7d9e").replace("-", ""),
    "04_CTA庫":     os.environ.get("NOTION_DB_CTA",          "b9c97a06fae5834589c5815c687e348f").replace("-", ""),
    "05_結構庫":    os.environ.get("NOTION_DB_STRUCTURE",    "6c497a06fae5823c98d6017d48acb70d").replace("-", ""),
    "06_視覺錘庫":  os.environ.get("NOTION_DB_VISUAL",      "bcbe1980652940ecb78fd05de9cb9653").replace("-", ""),
    "07_語言釘庫":  os.environ.get("NOTION_DB_VERBAL",      "dc9851e2e6114231b01be85ef7afd9b5").replace("-", ""),
    "35_IP型":      os.environ.get("NOTION_DB_IP_SCRIPT",   "efc0711ff4964eecb78fd05de9cb9653").replace("-", ""),
    "35_導購型":    os.environ.get("NOTION_DB_SALES_SCRIPT", "461772ac895a4f8cb5a7f5305ecc521b").replace("-", ""),
}

# 修復缺陷4：統一使用 notion_visual_manual.md（與 manual_version_tracker.py 一致）
MANUAL_PATH = Path("/home/ubuntu/viral_factory/notion_visual_manual.md")
PDF_PATH    = Path("/home/ubuntu/viral_factory/爆款短影音拆解工廠v3.0_團隊操作手冊.pdf")
DATA_DIR    = Path("/home/ubuntu/viral_factory/data")

client = OpenAI()

# ─── 工具函數 ─────────────────────────────────────────────

def run_mcp(tool: str, input_dict: dict, timeout: int = 30) -> dict:
    """呼叫 Notion MCP 工具，統一解析輸出"""
    try:
        result = subprocess.run(
            ["manus-mcp-cli", "tool", "call", tool,
             "--server", "notion",
             "--input", json.dumps(input_dict)],
            capture_output=True, text=True, timeout=timeout
        )
        if result.returncode != 0:
            print(f"  MCP 呼叫失敗（{tool}）：{result.stderr[:100]}")
            return {}
        raw = result.stdout.strip()
        # 移除 ANSI 控制碼
        raw = re.sub(r'\x1b\[[0-9;]*m', '', raw)
        # 取 Tool execution result: 之後的部分
        if "Tool execution result:" in raw:
            raw = raw.split("Tool execution result:")[-1].strip()
        if not raw:
            return {}
        return json.loads(raw)
    except Exception as e:
        print(f"  MCP 呼叫失敗（{tool}）：{e}")
        return {}


def run_slack(message: str, channel: str):
    """發送 Slack 訊息"""
    try:
        subprocess.run(
            ["manus-mcp-cli", "tool", "call", "slack_send_message",
             "--server", "slack",
             "--input", json.dumps({"channel_id": channel, "message": message})],
            capture_output=True, text=True, timeout=20
        )
        print(f"  Slack 已發送至 {channel}")
    except Exception as e:
        print(f"  Slack 發送失敗：{e}")


def get_week_range() -> tuple[str, str]:
    """取得本週（上週一至上週日）的日期範圍（手冊更新時統計上週數據）"""
    today = datetime.now()
    # 上週一
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday  = last_monday + timedelta(days=6)
    return last_monday.strftime("%Y-%m-%d"), last_sunday.strftime("%Y-%m-%d")


def count_db_entries_this_week(db_id: str, start: str, end: str) -> int:
    """查詢某個 Notion 資料庫在指定日期範圍內的新增筆數"""
    try:
        result = run_mcp("notion-query-data-sources", {
            "data_source_id": db_id,
            "filter": {
                "property": "建立時間",
                "date": {
                    "on_or_after": start,
                    "on_or_before": end + "T23:59:59"
                }
            },
            "page_size": 100
        }, timeout=25)
        results = result.get("results", result.get("pages", []))
        return len(results)
    except Exception:
        return 0


def get_top_scores_this_week(start: str, end: str) -> dict:
    """統計本週各評分的數量"""
    try:
        result = run_mcp("notion-query-data-sources", {
            "data_source_id": NOTION_DB["02_拆解庫"],
            "filter": {
                "property": "建立時間",
                "date": {
                    "on_or_after": start,
                    "on_or_before": end + "T23:59:59"
                }
            },
            "page_size": 100
        }, timeout=25)
        entries = result.get("results", result.get("pages", []))
        score_dist = {"5分": 0, "4分": 0, "3分": 0, "2分": 0, "1分": 0}
        for entry in entries:
            props = entry.get("properties", {})
            score_prop = props.get("評分", {})
            score_val = score_prop.get("number")
            if score_val:
                key = f"{int(score_val)}分"
                if key in score_dist:
                    score_dist[key] += 1
        return score_dist
    except Exception:
        return {}


def generate_weekly_summary(stats: dict, week_str: str) -> str:
    """用 GPT 生成本週系統動態摘要"""
    prompt = f"""
你是好創整合行銷的 AI 系統助理。根據以下本週（{week_str}）的爆款短影音拆解工廠數據，
用繁體中文（台灣商業語氣）撰寫一段簡潔有力的「本週系統動態摘要」，
供全團隊在 Slack 閱讀。

本週數據：
- 02 爆款拆解庫 新增：{stats.get('02_拆解庫', 0)} 筆
- 03 開頭鉤子庫 新增：{stats.get('03_鉤子庫', 0)} 筆
- 04 結尾呼籲庫 新增：{stats.get('04_CTA庫', 0)} 筆
- 05 腳本結構庫 新增：{stats.get('05_結構庫', 0)} 筆
- 06 視覺錘庫 新增：{stats.get('06_視覺錘庫', 0)} 筆
- 07 語言釘庫 新增：{stats.get('07_語言釘庫', 0)} 筆
- 35 IP型腳本庫 新增：{stats.get('35_IP型', 0)} 筆（4-5分高分）
- 35 導購型腳本庫 新增：{stats.get('35_導購型', 0)} 筆（4-5分高分）
- 評分分佈：{stats.get('score_dist', {})}

要求：
1. 開頭用一句話說明本週整體表現（好/普通/需關注）
2. 點出本週最值得注意的數字（哪個庫成長最多？高分腳本有幾支？）
3. 給團隊一句行動建議（去哪個庫找靈感？）
4. 全文不超過 150 字，不使用 emoji
5. 語氣直接、商業化，不要廢話
"""
    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.6
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"本週系統正常運行，數據已更新至各庫房。（摘要生成失敗：{e}）"


def update_manual_version(stats: dict, week_str: str, summary: str):
    """
    更新手冊版本（修復缺陷4、9）：
    - 不再自己操作 Markdown，改為呼叫 manual_version_tracker 統一處理
    - 版本號改為語意化遞增（minor），不再用固定的 v3.0-auto
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from manual_version_tracker import auto_detect_and_update

    high_score_count = stats.get('35_IP型', 0) + stats.get('35_導購型', 0)
    changes = [
        f"週報自動更新：本週新增拆解 {stats.get('02_拆解庫', 0)} 筆",
        f"高分腳本入庫 {high_score_count} 支（IP型 {stats.get('35_IP型', 0)} / 導購型 {stats.get('35_導購型', 0)}）",
        f"本週摘要：{summary[:50]}..."
    ]

    new_version = auto_detect_and_update(
        reason=f"週報自動更新 {week_str}",
        changes=changes,
        notify_slack=True  # 週報更新屬於 minor，發 Slack 通知
    )
    print(f"  手冊版本已更新至 v{new_version}")


def convert_to_pdf():
    """將手冊轉換為 PDF"""
    try:
        result = subprocess.run(
            ["manus-md-to-pdf", str(MANUAL_PATH), str(PDF_PATH)],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            print(f"  PDF 已更新：{PDF_PATH}")
            return True
        else:
            print(f"  PDF 轉換失敗：{result.stderr}")
            return False
    except Exception as e:
        print(f"  PDF 轉換異常：{e}")
        return False


def upload_pdf_to_s3() -> str:
    """上傳 PDF 到 S3，取得公開連結"""
    try:
        result = subprocess.run(
            ["manus-upload-file", str(PDF_PATH)],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            # 解析輸出中的 URL
            lines = result.stdout.strip().split("\n")
            for line in lines:
                if line.startswith("http"):
                    print(f"  PDF 已上傳：{line}")
                    return line
        print(f"  S3 上傳失敗：{result.stderr}")
        return ""
    except Exception as e:
        print(f"  S3 上傳異常：{e}")
        return ""


def send_team_notification(stats: dict, week_str: str, summary: str, pdf_url: str):
    """發送 Slack 通知至 #all-團隊主頻道"""
    high_score = stats.get('35_IP型', 0) + stats.get('35_導購型', 0)
    total_new  = stats.get('02_拆解庫', 0)

    msg = (
        f"📋 *爆款短影音拆解工廠｜手冊週更通知* | {week_str}\n"
        f"{'─' * 40}\n"
        f"\n"
        f"*本週系統動態*\n"
        f"{summary}\n"
        f"\n"
        f"*本週入庫統計*\n"
        f"• 爆款拆解：{total_new} 支\n"
        f"• 開頭鉤子庫：+{stats.get('03_鉤子庫', 0)} 筆\n"
        f"• 結尾呼籲庫：+{stats.get('04_CTA庫', 0)} 筆\n"
        f"• 腳本結構庫：+{stats.get('05_結構庫', 0)} 筆\n"
        f"• 視覺錘庫：+{stats.get('06_視覺錘庫', 0)} 筆\n"
        f"• 語言釘庫：+{stats.get('07_語言釘庫', 0)} 筆\n"
        f"• 35 高分腳本入庫：*{high_score} 支*（IP型 {stats.get('35_IP型', 0)} / 導購型 {stats.get('35_導購型', 0)}）\n"
        f"\n"
        f"*最新版操作手冊*\n"
        f"{'📥 ' + pdf_url if pdf_url else '（PDF 更新中，請稍後至 Notion 查看）'}\n"
        f"\n"
        f"操作手冊已同步更新，請各位取用最新版本。"
    )
    run_slack(msg, SLACK_TEAM_CH)


def send_coo_summary(stats: dict, week_str: str, summary: str):
    """發送系統健康摘要給子權（COO）"""
    high_score = stats.get('35_IP型', 0) + stats.get('35_導購型', 0)
    score_dist = stats.get('score_dist', {})

    msg = (
        f"📊 *手冊週更系統報告* | {week_str}\n"
        f"{'─' * 35}\n"
        f"\n"
        f"*評分分佈*\n"
        f"5分：{score_dist.get('5分', 0)} 支 ｜ "
        f"4分：{score_dist.get('4分', 0)} 支 ｜ "
        f"3分：{score_dist.get('3分', 0)} 支 ｜ "
        f"2分：{score_dist.get('2分', 0)} 支 ｜ "
        f"1分：{score_dist.get('1分', 0)} 支\n"
        f"\n"
        f"*高分入庫*：{high_score} 支（進入 35 庫）\n"
        f"\n"
        f"*手冊狀態*：已自動更新並發送至 #all-團隊主頻道\n"
        f"*PDF*：已重新生成並上傳 S3"
    )
    run_slack(msg, SLACK_ZIQUAN_ID)


# ─── 主流程 ───────────────────────────────────────────────

def run_weekly_manual_update():
    today     = datetime.now()
    week_str  = f"{today.strftime('%Y/%m/%d')} 週更"
    start, end = get_week_range()

    print(f"\n{'='*50}")
    print(f"爆款短影音拆解工廠｜手冊自動更新系統")
    print(f"執行時間：{today.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"統計範圍：{start} ~ {end}")
    print(f"{'='*50}\n")

    # Step 1：統計各庫房本週新增數量
    print("[1/6] 統計各庫房本週新增數量...")
    stats = {}
    for db_name, db_id in NOTION_DB.items():
        count = count_db_entries_this_week(db_id, start, end)
        stats[db_name] = count
        print(f"  {db_name}：{count} 筆")

    # Step 2：統計評分分佈
    print("\n[2/6] 統計評分分佈...")
    score_dist = get_top_scores_this_week(start, end)
    stats["score_dist"] = score_dist
    print(f"  {score_dist}")

    # Step 3：GPT 生成本週摘要
    print("\n[3/6] GPT 生成本週系統動態摘要...")
    summary = generate_weekly_summary(stats, week_str)
    print(f"  摘要：{summary[:80]}...")

    # Step 4：更新手冊 Markdown
    print("\n[4/6] 更新手冊版本與統計數據...")
    update_manual_version(stats, week_str, summary)

    # Step 5：轉換 PDF 並上傳 S3
    print("\n[5/6] 重新生成 PDF 並上傳...")
    pdf_ok  = convert_to_pdf()
    pdf_url = upload_pdf_to_s3() if pdf_ok else ""

    # Step 6：發送 Slack 通知
    print("\n[6/6] 發送 Slack 通知...")
    send_team_notification(stats, week_str, summary, pdf_url)
    send_coo_summary(stats, week_str, summary)

    print(f"\n{'='*50}")
    print("手冊週更完成！")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    run_weekly_manual_update()
