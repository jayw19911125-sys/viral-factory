import os
import json
from pathlib import Path
from datetime import datetime

# 動態計算路徑，避免硬編碼 /home/ubuntu 導致環境移植失敗
TRACKER_FILE = str(Path(__file__).parent / "data" / "execution_tracker.json")

def init_tracker():
    if not os.path.exists(os.path.dirname(TRACKER_FILE)):
        os.makedirs(os.path.dirname(TRACKER_FILE))
    if not os.path.exists(TRACKER_FILE):
        with open(TRACKER_FILE, 'w', encoding='utf-8') as f:
            json.dump({}, f)

def log_delivery(video_id, user_role, status="sent"):
    init_tracker()
    with open(TRACKER_FILE, 'r+', encoding='utf-8') as f:
        data = json.load(f)
        today = datetime.now().strftime("%Y-%m-%d")
        if today not in data:
            data[today] = {}
        if video_id not in data[today]:
            data[today][video_id] = {}
        
        data[today][video_id][user_role] = {
            "status": status,
            "delivered_at": datetime.now().isoformat(),
            "confirmed_at": None
        }
        f.seek(0)
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.truncate()

def get_daily_report(date_str=None):
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    init_tracker()
    with open(TRACKER_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
        return data.get(date_str, {})

if __name__ == "__main__":
    # 測試
    log_delivery("test_vid_123", "planner")
    print(json.dumps(get_daily_report(), indent=2, ensure_ascii=False))
