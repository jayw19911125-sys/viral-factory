"""
爆款短影音週報分析系統 v1.1
好創整合行銷 | 子權 2026-06-09
修復：2026-06-26 - 修正 Notion MCP 工具名稱與解析邏輯

執行時機：每週五 10:00（Manus 排程）
流程：Notion 爆款拆解庫（本週資料）→ GPT 規律分析 → Slack #all-團隊主頻道
"""

import os
import re
import json
import subprocess
from datetime import datetime, timedelta
from openai import OpenAI

# ─── 設定區 ───────────────────────────────────────────────
NOTION_DB_ID     = os.environ.get("NOTION_DB_MAIN", "82097a06-fae5-83bd-a8c3-87236d3713aa")
SLACK_TEAM_CH    = os.environ.get("SLACK_TEAM_CH",  "C0AQG307XJT")   # #all-團隊主頻道

WEEKLY_ANALYSIS_PROMPT = """
你是好創整合行銷的短影音策略顧問，專門分析台灣市場的爆款短影音規律。

以下是本週入庫的 {count} 支爆款影片拆解資料：

{data}

請根據這些資料，進行跨影片的規律分析，以 JSON 格式回傳以下內容：

{{
  "本週最強Hook類型": "出現最多次的 Hook 手法，並說明為何有效（50字以內）",
  "共同爆款結構": "這些影片共同的敘事框架或節奏規律（100字以內）",
  "視覺共同規律": "畫面、剪輯、字幕風格的共同特徵（50字以內）",
  "受眾心理共鳴點": "這些影片觸動了台灣受眾的哪些共同情感或需求（50字以內）",
  "下週建議拍攝方向": [
    "具體建議1（含 Hook 類型 + 主題方向，30字以內）",
    "具體建議2（含 Hook 類型 + 主題方向，30字以內）",
    "具體建議3（含 Hook 類型 + 主題方向，30字以內）"
  ],
  "本週最值得借鏡的影片": "標題或主題，以及最值得借鏡的具體原因（50字以內）",
  "數據摘要": {{
    "入庫支數": {count},
    "平均觀看數": "從資料中估算",
    "最高觀看數": "從資料中找出最高值",
    "最常出現帳號": "出現最多次的帳號名稱"
  }}
}}
"""

def _parse_mcp_output(stdout: str) -> dict:
    """統一解析 manus-mcp-cli 的輸出，處理各種格式"""
    raw = stdout.strip()
    # 移除 ANSI 控制碼
    raw = re.sub(r'\x1b\[[0-9;]*m', '', raw)
    # 取 Tool execution result: 之後的部分
    if "Tool execution result:" in raw:
        raw = raw.split("Tool execution result:")[-1].strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}

def get_this_week_entries() -> list:
    """從 Notion 爆款拆解庫讀取本週入庫的影片"""
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    monday_str = monday.strftime("%Y-%m-%d")

    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-query-data-sources",
        "--server", "notion",
        "--input", json.dumps({
            "data_source_id": NOTION_DB_ID,
            "filter": {
                "property": "入庫日期",
                "date": {"on_or_after": monday_str}
            },
            "sorts": [{"property": "入庫日期", "direction": "descending"}]
        })
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"Notion 查詢失敗：{result.stderr[:200]}")
        return []

    data = _parse_mcp_output(result.stdout)
    if not data:
        print("Notion 回傳空值，嘗試備用查詢...")
        return _fallback_search_entries(monday_str)
    # notion-query-data-sources 回傳格式
    return data.get("results", data.get("pages", []))


def _fallback_search_entries(monday_str: str) -> list:
    """備用方案：用 notion-search 搜尋本週入庫的影片"""
    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-search",
        "--server", "notion",
        "--input", json.dumps({
            "query": "爆款拆解",
            "filters": {
                "created_date_range": {
                    "start_date": monday_str
                }
            },
            "page_size": 25
        })
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return []
    data = _parse_mcp_output(result.stdout)
    return data.get("results", [])


def fetch_page_content(page_id: str) -> str:
    """讀取單一 Notion 頁面的內容（拆解詳情）"""
    page_url = f"https://www.notion.so/{page_id.replace('-', '')}"
    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-fetch",
        "--server", "notion",
        "--input", json.dumps({"url": page_url})
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if result.returncode != 0:
        return ""
    try:
        raw = result.stdout.strip()
        raw = re.sub(r'\x1b\[[0-9;]*m', '', raw)
        if "Tool execution result:" in raw:
            raw = raw.split("Tool execution result:")[-1].strip()
        # notion-fetch 直接回傳 Markdown 文字
        return raw[:1500]
    except Exception:
        return ""


def build_analysis_data(entries: list) -> str:
    """將 Notion 條目整理成 GPT 可分析的文字格式"""
    lines = []
    for i, entry in enumerate(entries, 1):
        props = entry.get("properties", {})

        def get_text(prop_name):
            prop = props.get(prop_name, {})
            ptype = prop.get("type", "")
            if ptype == "title":
                items = prop.get("title", [])
            elif ptype == "rich_text":
                items = prop.get("rich_text", [])
            else:
                return ""
            return "".join(t.get("plain_text", "") for t in items)

        def get_select(prop_name):
            prop = props.get(prop_name, {})
            sel = prop.get("select", {})
            return sel.get("name", "") if sel else ""

        def get_number(prop_name):
            prop = props.get(prop_name, {})
            return prop.get("number", 0) or 0

        title     = get_text("影片標題或主題")
        platform  = get_select("平台")
        hook      = get_text("開頭鉤子拆解")
        structure = get_text("結構拆解")
        why_boom  = get_text("為什麼會爆")
        views     = get_number("觀看數")

        lines.append(
            f"[影片{i}] {title}\n"
            f"  平台：{platform} | 觀看數：{views:,}\n"
            f"  Hook：{hook[:100]}\n"
            f"  結構：{structure[:100]}\n"
            f"  爆款原因：{why_boom[:150]}\n"
        )

    return "\n".join(lines)


def analyze_with_gpt(data_text: str, count: int) -> dict:
    """用 GPT 分析跨影片共同規律"""
    client = OpenAI()
    prompt = WEEKLY_ANALYSIS_PROMPT.format(data=data_text, count=count)
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.3
    )
    return json.loads(response.choices[0].message.content)


def send_weekly_report_to_slack(analysis: dict, count: int, week_str: str):
    """發送週報到 Slack 團隊主頻道"""
    suggestions = analysis.get("下週建議拍攝方向", [])
    suggestions_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(suggestions))

    stats = analysis.get("數據摘要", {})

    msg = (
        f"📊 *爆款短影音週報* | {week_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*本週入庫：{count} 支* | 最高觀看：{stats.get('最高觀看數', 'N/A')} | 最常出現：{stats.get('最常出現帳號', 'N/A')}\n\n"
        f"🎣 *本週最強 Hook 類型*\n{analysis.get('本週最強Hook類型', '')}\n\n"
        f"🏗️ *共同爆款結構*\n{analysis.get('共同爆款結構', '')}\n\n"
        f"👁️ *視覺共同規律*\n{analysis.get('視覺共同規律', '')}\n\n"
        f"❤️ *受眾心理共鳴點*\n{analysis.get('受眾心理共鳴點', '')}\n\n"
        f"⭐ *本週最值得借鏡*\n{analysis.get('本週最值得借鏡的影片', '')}\n\n"
        f"🎬 *下週建議拍攝方向*\n{suggestions_text}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"_完整拆解庫：https://app.notion.com/p/82097a06fae583bda8c387236d3713aa_"
    )

    cmd = [
        "manus-mcp-cli", "tool", "call", "slack_send_message",
        "--server", "slack",
        "--input", json.dumps({
            "channel_id": SLACK_TEAM_CH,
            "message": msg
        })
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"Slack 發送失敗：{result.stderr[:100]}")
        return False
    print("Slack 週報已發送至 #all-團隊主頻道")
    return True


def run_weekly_report():
    """週報主流程"""
    today = datetime.now()
    week_str = f"{today.strftime('%Y/%m/%d')} 週報"

    print(f"\n{'='*55}")
    print(f"爆款短影音週報分析 | {week_str}")
    print(f"{'='*55}")

    # Step 1: 讀取本週資料
    print("\n[1/3] 讀取本週 Notion 爆款拆解庫...")
    entries = get_this_week_entries()

    if len(entries) < 3:
        msg = (
            f"📊 *爆款短影音週報* | {week_str}\n\n"
            f"⚠️ 本週入庫資料不足（{len(entries)} 支），無法進行規律分析。\n"
            f"建議確認每日拆解排程是否正常執行。"
        )
        cmd = [
            "manus-mcp-cli", "tool", "call", "slack_send_message",
            "--server", "slack",
            "--input", json.dumps({"channel_id": SLACK_TEAM_CH, "message": msg})
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        print(f"資料不足（{len(entries)} 支），已發送提醒至 Slack")
        return

    print(f"  讀取到 {len(entries)} 支本週入庫影片")

    # Step 2: GPT 規律分析
    print("\n[2/3] GPT 跨影片規律分析...")
    data_text = build_analysis_data(entries)
    analysis = analyze_with_gpt(data_text, len(entries))
    print(f"  分析完成：本週最強 Hook = {analysis.get('本週最強Hook類型', '')[:40]}")

    # Step 3: 發送 Slack 週報
    print("\n[3/3] 發送週報至 Slack 團隊主頻道...")
    send_weekly_report_to_slack(analysis, len(entries), week_str)

    print(f"\n{'='*55}")
    print(f"✅ 週報完成")


if __name__ == "__main__":
    run_weekly_report()
