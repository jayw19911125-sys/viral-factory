"""Build a factual Slack delivery report without calling delivery 'completion'."""

from __future__ import annotations

from datetime import datetime

from execution_tracker import get_daily_report


def _role_counts(report_data: dict, role_name: str) -> dict[str, int]:
    rows = [video.get(role_name, {}) for video in report_data.values()]
    return {
        "attempted": sum(1 for row in rows if row),
        "api_accepted": sum(
            1 for row in rows
            if row.get("status") in {"trackable_sent", "sent_untracked", "delivered", "sent", "confirmed"}
        ),
        "trackable": sum(1 for row in rows if row.get("message_ts")),
        "confirmed": sum(1 for row in rows if row.get("status") == "confirmed" and row.get("confirmed_at")),
    }


def generate_management_report() -> str:
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    report_data = get_daily_report(today)
    if not report_data:
        return f"📊 *【軍師管理日報】Slack 通知追蹤* | {today}\n⚠️ 今日沒有可驗證的通知事件。"

    total_videos = len(report_data)
    planner = _role_counts(report_data, "planner")
    editor = _role_counts(report_data, "editor")

    return (
        f"📊 *【軍師管理日報】Slack 通知追蹤* | {today}\n"
        f"今日有通知事件的唯一影片：*{total_videos}* 支\n\n"
        f"👤 *小鑫（企劃）*\n"
        f"• 發送嘗試：{planner['attempted']}/{total_videos}\n"
        f"• API 成功碼：{planner['api_accepted']}/{total_videos}\n"
        f"• 可綁定訊息 ID：{planner['trackable']}/{total_videos}\n"
        f"• 已確認：{planner['confirmed']}/{total_videos}\n\n"
        f"👤 *阿韋（剪輯）*\n"
        f"• 發送嘗試：{editor['attempted']}/{total_videos}\n"
        f"• API 成功碼：{editor['api_accepted']}/{total_videos}\n"
        f"• 可綁定訊息 ID：{editor['trackable']}/{total_videos}\n"
        f"• 已確認：{editor['confirmed']}/{total_videos}\n\n"
        "⚠️ 本報只代表 Slack 訊息事件，不代表已讀、工作完成或績效。"
    )


if __name__ == "__main__":
    print(generate_management_report())
