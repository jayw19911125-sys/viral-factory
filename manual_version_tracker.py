"""
manual_version_tracker.py
爆款短影音拆解工廠｜手冊版本管理核心模組

版本號規則：
  主版本（v4.0）：系統架構重大變更（新增/移除核心功能）
  次版本（v3.1）：功能更新（新增產業、新增庫房、流程調整）
  修訂版（v3.0.1）：資料更新（每日拆解統計、庫房數量變化）

觸發機制：
  程式碼層面：任何腳本修改後手動呼叫 bump_version("minor", reason="...")
  資料層面：daily_run.py 每日完成後自動呼叫 bump_version("patch", reason="...")
"""

import json
import os
import subprocess
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ─── 設定 ─────────────────────────────────────────────────
BASE_DIR        = Path(__file__).parent
VERSION_FILE    = BASE_DIR / ".manual_version.json"
MANUAL_MD       = BASE_DIR / "notion_visual_manual.md"
NOTION_PAGE_ID  = "37f97a06-fae5-813e-966d-c762a2bb7eb6"

# 監控的腳本檔案（任何一個 hash 變化 = 程式碼層面更動）
WATCHED_FILES = [
    "viral_factory.py",
    "meta_ads_fetcher.py",
    "script_scorer.py",
    "script_distributor.py",
    "daily_run.py",
    "weekly_report.py",
    "weekly_manual_updater.py",
    "monitor_accounts.json",
]

# Slack 設定
SLACK_TEAM_CH = "C0AQG307XJT"   # #all-團隊主頻道
SLACK_AUTO_CH = "C0AUH4QKF5M"   # #自動化訊息來源

# 台灣時區
TW_TZ = timezone(timedelta(hours=8))


# ─── 版本檔案管理 ─────────────────────────────────────────

def load_version_data() -> dict:
    """載入版本記錄檔，若不存在則初始化"""
    if VERSION_FILE.exists():
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    # 初始化
    data = {
        "version": "3.0.2",
        "major": 3,
        "minor": 0,
        "patch": 2,
        "last_updated": datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M"),
        "file_hashes": {},
        "changelog": [
            {
                "version": "3.0.0",
                "date": "2026-06-12",
                "type": "major",
                "changes": ["系統初始建立：雙來源抓片、12欄位拆解、評分分配"]
            },
            {
                "version": "3.0.1",
                "date": "2026-06-13",
                "type": "minor",
                "changes": ["新增 weekly_manual_updater.py 手冊自動週更系統"]
            },
            {
                "version": "3.0.2",
                "date": "2026-06-15",
                "type": "minor",
                "changes": [
                    "Slack 通知改為 #自動化訊息來源 頻道並 @阿韋",
                    "Meta 產業清單從 12 個擴充至 28 個",
                    "手冊新增 6 大鉤子類型、6 種人設類型說明",
                    "手冊新增所有庫房 Notion 直接連結",
                    "修正 TikTok/Reels 監控說明（目前僅 TikTok）"
                ]
            }
        ]
    }
    save_version_data(data)
    return data


def save_version_data(data: dict):
    """儲存版本記錄檔"""
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_file_hash(filepath: Path) -> str:
    """計算檔案 MD5 hash"""
    if not filepath.exists():
        return ""
    with open(filepath, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def check_code_changes() -> list:
    """
    檢查監控的腳本是否有變動
    回傳：有變動的檔案清單
    """
    data = load_version_data()
    stored_hashes = data.get("file_hashes", {})
    changed = []

    for fname in WATCHED_FILES:
        fpath = BASE_DIR / fname
        current_hash = get_file_hash(fpath)
        if current_hash and current_hash != stored_hashes.get(fname, ""):
            changed.append(fname)

    return changed


def update_file_hashes(data: dict):
    """更新所有監控檔案的 hash"""
    hashes = {}
    for fname in WATCHED_FILES:
        fpath = BASE_DIR / fname
        h = get_file_hash(fpath)
        if h:
            hashes[fname] = h
    data["file_hashes"] = hashes


# ─── 版本號遞增 ───────────────────────────────────────────

def bump_version(bump_type: str, reason: str, changes: list = None) -> str:
    """
    遞增版本號並記錄 changelog

    bump_type: "major" | "minor" | "patch"
    reason: 本次更新的主要原因（一句話）
    changes: 詳細變更清單（list of str）

    回傳：新版本號字串
    """
    data = load_version_data()
    now = datetime.now(TW_TZ)

    if bump_type == "major":
        data["major"] += 1
        data["minor"] = 0
        data["patch"] = 0
    elif bump_type == "minor":
        data["minor"] += 1
        data["patch"] = 0
    else:  # patch
        data["patch"] += 1

    new_version = f"{data['major']}.{data['minor']}.{data['patch']}"
    data["version"] = new_version
    data["last_updated"] = now.strftime("%Y-%m-%d %H:%M")

    # 記錄 changelog
    entry = {
        "version": new_version,
        "date": now.strftime("%Y-%m-%d"),
        "type": bump_type,
        "changes": changes or [reason]
    }
    data["changelog"].insert(0, entry)  # 最新的放最前面

    # 更新 file hashes
    update_file_hashes(data)
    save_version_data(data)

    print(f"  📌 版本更新：v{new_version}（{bump_type}）")
    return new_version


# ─── Notion 手冊更新 ──────────────────────────────────────

def build_changelog_table(changelog: list) -> str:
    """將 changelog 轉為 Notion 表格格式"""
    type_label = {"major": "🔴 重大更新", "minor": "🟡 功能更新", "patch": "🟢 資料更新"}

    rows = []
    rows.append("<table header-row=\"true\">")
    rows.append("  <tr>")
    rows.append("    <td>版本號</td>")
    rows.append("    <td>日期</td>")
    rows.append("    <td>類型</td>")
    rows.append("    <td>更新內容</td>")
    rows.append("  </tr>")

    for entry in changelog[:20]:  # 最多顯示 20 筆
        label = type_label.get(entry.get("type", "patch"), "🟢 資料更新")
        changes_text = " / ".join(entry.get("changes", []))
        rows.append("  <tr>")
        rows.append(f"    <td>v{entry['version']}</td>")
        rows.append(f"    <td>{entry['date']}</td>")
        rows.append(f"    <td>{label}</td>")
        rows.append(f"    <td>{changes_text}</td>")
        rows.append("  </tr>")

    rows.append("</table>")
    return "\n".join(rows)


def update_notion_manual(version: str, changelog: list):
    """更新 Notion 手冊頁面的版本資訊與 changelog"""
    now = datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M")

    # 讀取現有手冊 Markdown
    manual_path = BASE_DIR / "notion_visual_manual.md"
    if not manual_path.exists():
        print("  ⚠️  手冊 Markdown 不存在，跳過 Notion 更新")
        return

    with open(manual_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 更新頂部的版本資訊行
    import re
    new_header = f"> **版本**：v{version} | **更新日期**：{now} | **適用對象**：涵勻、阿韋、子權 | **系統狀態**：全自動運行中（每週一至週五 09:00 自動觸發）"

    # 替換第一行（版本資訊行）
    lines = content.split("\n")
    if lines[0].startswith(">"):
        lines[0] = new_header
    else:
        lines.insert(0, new_header)

    # 建立版本歷程章節
    changelog_section = f"""
---

## 玖、版本歷程

本手冊隨系統自動更新。每次腳本修改或每日拆解完成後，系統會自動遞增版本號並記錄變更內容。

{build_changelog_table(changelog)}
"""

    # 移除舊的版本歷程章節（如果存在）
    content_new = "\n".join(lines)
    if "## 玖、版本歷程" in content_new:
        content_new = content_new[:content_new.index("## 玖、版本歷程") - 5]

    content_new = content_new.rstrip() + changelog_section

    # 寫回 Markdown 檔案
    with open(manual_path, "w", encoding="utf-8") as f:
        f.write(content_new)

    # 同步到 Notion
    payload = {
        "page_id": NOTION_PAGE_ID,
        "command": "replace_content",
        "new_str": content_new
    }

    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-update-page",
        "--server", "notion",
        "--input", json.dumps(payload, ensure_ascii=False)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode == 0:
        print(f"  ✅ Notion 手冊已更新至 v{version}")
    else:
        print(f"  ⚠️  Notion 更新失敗：{result.stderr[:100]}")


def send_slack_version_notice(version: str, bump_type: str, changes: list):
    """發送版本更新通知到 Slack"""
    type_emoji = {"major": "🔴", "minor": "🟡", "patch": "🟢"}
    emoji = type_emoji.get(bump_type, "🟢")
    now = datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M")

    changes_text = "\n".join([f"  • {c}" for c in changes])
    msg = (
        f"{emoji} *爆款拆解工廠手冊更新* | v{version} | {now}\n\n"
        f"*更新內容：*\n{changes_text}\n\n"
        f"📋 查看最新手冊：https://app.notion.com/p/37f97a06fae5813e966dc762a2bb7eb6"
    )

    payload = {"channel_id": SLACK_TEAM_CH, "message": msg}
    cmd = [
        "manus-mcp-cli", "tool", "call", "slack_send_message",
        "--server", "slack",
        "--input", json.dumps(payload, ensure_ascii=False)
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=30)


# ─── 主要對外介面 ─────────────────────────────────────────

def auto_detect_and_update(reason: str = "", changes: list = None, notify_slack: bool = True):
    """
    自動偵測程式碼變動並更新手冊（給 daily_run.py 呼叫）

    - 若有腳本變動 → bump minor
    - 若只有資料更新（每日拆解）→ bump patch
    """
    changed_files = check_code_changes()

    if changed_files:
        bump_type = "minor"
        auto_changes = [f"腳本更新：{', '.join(changed_files)}"]
        if changes:
            auto_changes.extend(changes)
        if reason:
            auto_changes.insert(0, reason)
    else:
        bump_type = "patch"
        auto_changes = changes or [reason or "每日拆解資料更新"]

    data = load_version_data()
    new_version = bump_version(bump_type, reason or auto_changes[0], auto_changes)

    # 更新 Notion 手冊
    update_notion_manual(new_version, data["changelog"])

    # Slack 通知（patch 版本不發通知，避免每天打擾）
    if notify_slack and bump_type != "patch":
        send_slack_version_notice(new_version, bump_type, auto_changes)

    return new_version


def manual_bump(bump_type: str, reason: str, changes: list = None, notify_slack: bool = True):
    """
    手動觸發版本更新（給腳本修改後手動呼叫）

    用法：
        python3 manual_version_tracker.py minor "新增 IG Reels 監控功能"
    """
    data = load_version_data()
    new_version = bump_version(bump_type, reason, changes or [reason])
    update_notion_manual(new_version, data["changelog"])
    if notify_slack:
        send_slack_version_notice(new_version, bump_type, changes or [reason])
    return new_version


# ─── CLI 入口 ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3:
        # 手動呼叫：python3 manual_version_tracker.py minor "新增 IG Reels 監控"
        bump_type = sys.argv[1]  # major / minor / patch
        reason = sys.argv[2]
        changes = sys.argv[3:] if len(sys.argv) > 3 else None
        version = manual_bump(bump_type, reason, changes)
        print(f"\n✅ 手冊已更新至 v{version}")
    else:
        # 自動偵測模式
        version = auto_detect_and_update()
        print(f"\n✅ 手冊已更新至 v{version}")
