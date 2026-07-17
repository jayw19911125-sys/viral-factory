"""Deterministic weekly report with data-coverage gates."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timedelta
from statistics import mean
from typing import Any

from openai import OpenAI

NOTION_DB_ID = os.environ.get("NOTION_DB_MAIN", "82097a06-fae5-83bd-a8c3-87236d3713aa")
NOTION_DB_PAGE_URL = os.environ.get(
    "NOTION_DB_PAGE_URL",
    "https://app.notion.com/p/fea97a06fae5837ba6bc81f63cb5cafb",
)
SLACK_TEAM_CH = os.environ.get("SLACK_TEAM_CH", "C0AQG307XJT")
MIN_COVERAGE = float(os.environ.get("WEEKLY_MIN_COVERAGE", "0.8"))

WEEKLY_ANALYSIS_PROMPT = """
你是好創整合行銷的短影音策略顧問。
以下資料已經過唯一性、證據狀態與欄位覆蓋率驗證。

{data}

只分析資料中明確存在的 Hook、結構與文字內容。不得自行補充觀看數、
作者、畫面、音樂、留存率、完播率、互動率或市場代表性。

以 JSON 回傳：
{{
  "本週最強Hook類型": "50字內；無法判斷則填資料不足",
  "共同內容結構": "100字內；只依現有文字欄位",
  "下週可測試方向": [
    "把建議寫成可驗證假設，不宣稱必然有效"
  ],
  "限制": ["列出樣本與證據限制"]
}}
"""


class WeeklyDataError(RuntimeError):
    pass


def _parse_mcp_output(stdout: str) -> dict:
    raw = re.sub(r"\x1b\[[0-9;]*m", "", stdout.strip())
    if "Tool execution result:" in raw:
        raw = raw.rsplit("Tool execution result:", 1)[-1].strip()
    if not raw:
        raise WeeklyDataError("Notion 回傳空值")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WeeklyDataError(f"Notion 回傳無法解析：{exc}") from exc
    if not isinstance(data, dict) or data.get("error"):
        raise WeeklyDataError(f"Notion 回傳錯誤：{str(data)[:200]}")
    return data


def _query_page(monday_str: str, cursor: str | None = None) -> dict:
    today_str = datetime.now().astimezone().strftime("%Y-%m-%d")
    query: dict[str, Any] = {
        "data_source_id": NOTION_DB_ID,
        "filter": {
            "and": [
                {"property": "入庫日期", "date": {"on_or_after": monday_str}},
                {"property": "入庫日期", "date": {"on_or_before": today_str}},
            ]
        },
        "sorts": [{"property": "入庫日期", "direction": "ascending"}],
        "page_size": 100,
    }
    if cursor:
        query["start_cursor"] = cursor
    result = subprocess.run(
        [
            "manus-mcp-cli",
            "tool",
            "call",
            "notion-query-data-sources",
            "--server",
            "notion",
            "--input",
            json.dumps(query, ensure_ascii=False),
        ],
        capture_output=True,
        text=True,
        timeout=45,
    )
    if result.returncode != 0:
        raise WeeklyDataError(
            "Notion 週報查詢失敗；禁止使用未限定資料庫的備援搜尋："
            + result.stderr[:200]
        )
    return _parse_mcp_output(result.stdout)


def get_this_week_entries() -> list:
    monday = datetime.now().astimezone() - timedelta(days=datetime.now().astimezone().weekday())
    monday_str = monday.strftime("%Y-%m-%d")
    rows = []
    cursor = None
    seen_cursors = set()

    while True:
        data = _query_page(monday_str, cursor)
        page_rows = data.get("results", data.get("pages"))
        if not isinstance(page_rows, list):
            raise WeeklyDataError("Notion 回傳缺少 results/pages")
        rows.extend(page_rows)
        if not data.get("has_more"):
            return rows
        cursor = data.get("next_cursor")
        if not cursor or cursor in seen_cursors:
            raise WeeklyDataError("Notion 宣告 has_more，但缺少有效 next_cursor")
        seen_cursors.add(cursor)


def _property_value(entry: dict, name: str):
    prop = (entry.get("properties") or {}).get(name)
    if prop is None:
        return None
    if not isinstance(prop, dict):
        return prop
    ptype = prop.get("type")
    if ptype == "number":
        return prop.get("number")
    if ptype == "select":
        selected = prop.get("select")
        return selected.get("name") if isinstance(selected, dict) else None
    if ptype == "status":
        selected = prop.get("status")
        return selected.get("name") if isinstance(selected, dict) else None
    if ptype == "url":
        return prop.get("url")
    if ptype in {"title", "rich_text"}:
        items = prop.get(ptype) or []
        return "".join(item.get("plain_text", "") for item in items if isinstance(item, dict)) or None
    if ptype == "date":
        value = prop.get("date")
        return value.get("start") if isinstance(value, dict) else None
    for key in ("number", "url", "value"):
        if prop.get(key) is not None:
            return prop[key]
    return None


def deterministic_stats(entries: list) -> dict:
    unique = {}
    excluded_status_count = 0
    for entry in entries:
        status = _property_value(entry, "處理狀態")
        # Historical rows without an explicit outcome are unverified, not success.
        if status != "unique_success":
            excluded_status_count += 1
            continue
        identity = (
            _property_value(entry, "platform_video_id")
            or _property_value(entry, "原始連結")
            or entry.get("id")
        )
        if identity and identity not in unique:
            unique[identity] = entry

    rows = list(unique.values())
    count = len(rows)
    views = [_property_value(row, "觀看數") for row in rows]
    views = [
        value for value in views
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
    ]
    authors = [_property_value(row, "作者帳號") for row in rows]
    authors = [value for value in authors if value]
    hooks = [_property_value(row, "開頭鉤子拆解") for row in rows]
    hooks = [value for value in hooks if value]
    structures = [_property_value(row, "結構拆解") for row in rows]
    structures = [value for value in structures if value]
    verified = [
        row for row in rows
        if _property_value(row, "證據狀態") == "verified"
    ]

    def coverage(values):
        return len(values) / count if count else 0.0

    author_counter = Counter(authors)
    top_author_count = max(author_counter.values(), default=0)
    top_authors = sorted(
        author for author, occurrences in author_counter.items()
        if occurrences == top_author_count
    )
    stats = {
        "unique_count": count,
        "excluded_status_count": excluded_status_count,
        "deduplicated_count": max(0, len(entries) - excluded_status_count - count),
        "average_views": round(mean(views)) if views else None,
        "max_views": max(views) if views else None,
        "most_common_author": top_authors[0] if len(top_authors) == 1 else None,
        "most_common_author_tie": top_authors if len(top_authors) > 1 else [],
        "coverage": {
            "views": coverage(views),
            "authors": coverage(authors),
            "hooks": coverage(hooks),
            "structures": coverage(structures),
            "verified_evidence": coverage(verified),
        },
        "rows": rows,
    }
    return stats


def _analysis_text(stats: dict) -> str:
    lines = []
    for index, entry in enumerate(stats["rows"], 1):
        lines.append(
            f"[影片{index}] "
            f"標題={_property_value(entry, '影片標題或主題') or '未提供'} | "
            f"Hook={_property_value(entry, '開頭鉤子拆解') or '未提供'} | "
            f"結構={_property_value(entry, '結構拆解') or '未提供'}"
        )
    return "\n".join(lines)


def _coverage_ready(stats: dict) -> bool:
    required = ("views", "authors", "hooks", "structures", "verified_evidence")
    return stats["unique_count"] >= 3 and all(
        stats["coverage"][key] >= MIN_COVERAGE for key in required
    )


def analyze_patterns(stats: dict) -> dict:
    if not _coverage_ready(stats):
        raise WeeklyDataError("欄位覆蓋率或已驗證樣本數不足")
    client = OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        base_url=os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1"),
    )
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{
            "role": "user",
            "content": WEEKLY_ANALYSIS_PROMPT.format(data=_analysis_text(stats)),
        }],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=1200,
    )
    raw = response.choices[0].message.content
    result = json.loads(raw)
    if not isinstance(result.get("下週可測試方向"), list):
        raise WeeklyDataError("GPT 週報輸出格式不合法")
    return result


def _fmt_number(value):
    return f"{int(value):,}" if value is not None else "資料不足"


def send_weekly_report(stats: dict, analysis: dict | None, week_range: str) -> dict:
    from viral_factory import send_slack_dm

    coverage = stats["coverage"]
    coverage_text = " | ".join(f"{key}={value:.0%}" for key, value in coverage.items())
    if stats["most_common_author_tie"]:
        common_author_text = "並列，無單一最高（" + "、".join(stats["most_common_author_tie"]) + "）"
    else:
        common_author_text = stats["most_common_author"] or "資料不足"

    lines = [
        f"📊 *爆款短影音週報* | {week_range}",
        f"唯一有效入庫：*{stats['unique_count']}* 支",
        f"平均觀看：{_fmt_number(stats['average_views'])}",
        f"最高觀看：{_fmt_number(stats['max_views'])}",
        f"最常出現帳號：{common_author_text}",
        f"排除非明確成功：{stats['excluded_status_count']} | 去重排除：{stats['deduplicated_count']}",
        f"資料覆蓋率：{coverage_text}",
        "",
    ]

    if analysis is None:
        lines.append("⚠️ 資料未達發布門檻，本週不產生 Hook 規律、拍攝或剪輯建議。")
    else:
        lines.extend([
            f"🎣 *本週最強 Hook*\n{analysis.get('本週最強Hook類型', '資料不足')}",
            f"🏗️ *共同內容結構*\n{analysis.get('共同內容結構', '資料不足')}",
            "🧪 *下週可測試方向*",
        ])
        lines.extend(f"• {item}" for item in analysis.get("下週可測試方向", []))
        if analysis.get("限制"):
            lines.append("限制：" + "；".join(analysis["限制"]))

    lines.append(f"📚 完整資料庫：{NOTION_DB_PAGE_URL}")
    return send_slack_dm("\n".join(lines), channel=SLACK_TEAM_CH)


def run_weekly_report() -> dict:
    now = datetime.now().astimezone()
    monday = now - timedelta(days=now.weekday())
    week_range = f"{monday.strftime('%Y/%m/%d')}–{now.strftime('%Y/%m/%d')}"

    try:
        entries = get_this_week_entries()
        stats = deterministic_stats(entries)
        analysis = analyze_patterns(stats) if _coverage_ready(stats) else None
        send_result = send_weekly_report(stats, analysis, week_range)
        if not send_result.get("success"):
            raise WeeklyDataError(f"Slack 週報發送失敗：{send_result.get('error')}")
        return {
            "success": True,
            "status": "published" if analysis else "blocked_guidance",
            "stats": stats,
            "slack": send_result,
        }
    except Exception as exc:
        from viral_factory import send_slack_dm

        message = (
            f"📊 *爆款短影音週報* | {week_range}\n"
            f"⛔ 週報資料管線阻擋發布：{type(exc).__name__}: {exc}\n"
            "未產生最高觀看、常見帳號、Hook 規律或拍攝建議。"
        )
        send_result = send_slack_dm(message, channel=SLACK_TEAM_CH)
        return {
            "success": False,
            "status": "data_blocked",
            "error": str(exc),
            "slack": send_result,
        }


if __name__ == "__main__":
    print(json.dumps(run_weekly_report(), ensure_ascii=False, indent=2, default=str))
