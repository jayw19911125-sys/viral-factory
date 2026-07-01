import json
from datetime import datetime
from execution_tracker import get_daily_report

def generate_management_report():
    today = datetime.now().strftime("%Y-%m-%d")
    report_data = get_daily_report(today)
    
    if not report_data:
        return "⚠️ 今日尚未有通知發送記錄。"
    
    total_videos = len(report_data)
    planner_delivered = sum(1 for v in report_data.values() if v.get('planner', {}).get('status') == 'delivered')
    editor_delivered = sum(1 for v in report_data.values() if v.get('editor', {}).get('status') == 'delivered')
    
    report_msg = f"📊 *【軍師管理日報】團隊執行追蹤* | {today}\n"
    report_msg += f"今日共處理影片：*{total_videos}* 支\n\n"
    
    report_msg += "👤 *小鑫 (企劃)*：\n"
    report_msg += f"• 送達狀態：{planner_delivered}/{total_videos} ✅\n"
    report_msg += "• 待確認事項：請檢查 Slack 回覆狀態\n\n"
    
    report_msg += "👤 *阿韋 (剪輯)*：\n"
    report_msg += f"• 送達狀態：{editor_delivered}/{total_videos} ✅\n"
    report_msg += "• 待確認事項：請檢查 Slack 回覆狀態\n\n"
    
    report_msg += "💡 *軍師建議*：若送達數不符，請檢查系統日誌。提醒團隊養成點擊「OK」回覆的習慣，以利後續自動化統計。"
    
    return report_msg

if __name__ == "__main__":
    print(generate_management_report())
