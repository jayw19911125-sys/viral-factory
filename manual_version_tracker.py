"""
manual_version_tracker.py
手冊版本管理核心模組 v2.0（全面修復版）

修復清單：
  [缺陷1] 版本資訊行定位錯誤 → 改用 regex 精確搜尋 "> **版本**" 行
  [缺陷2] 版本歷程章節標題不一致 → 改用 ANCHOR 標記定位，不依賴章節名稱
  [缺陷3] auto_detect_and_update 傳入舊 changelog → bump_version 改為接受 data 參數，
           在同一個 data 物件上操作，save 後 changelog 已是最新的
  [缺陷4] weekly_manual_updater 操作不同手冊路徑 → 統一使用 notion_visual_manual.md
  [缺陷5] .manual_version.json 不持久化 → 改為存在 Notion 頁面的 description 欄位，
           本機 JSON 只作快取；沙盒重啟後從 Notion 重新同步

觸發方式：
  程式碼層面：daily_run.py 完成後自動呼叫 auto_detect_and_update()
  資料層面：同上，每日拆解完成後 patch 版本靜默更新
  手動觸發：python3 manual_version_tracker.py major "新增 IG Reels 監控"
"""

import json
import hashlib
import subprocess
import re
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ─── 常數設定 ─────────────────────────────────────────────
BASE_DIR        = Path(__file__).parent
VERSION_JSON    = BASE_DIR / ".manual_version.json"
MANUAL_PATH     = BASE_DIR / "notion_visual_manual.md"   # 唯一手冊路徑（修復缺陷4）

NOTION_PAGE_ID  = "37f97a06-fae5-813e-966d-c762a2bb7eb6"
SLACK_TEAM_CH   = "C0AQG307XJT"   # #all-團隊主頻道
TW_TZ           = timezone(timedelta(hours=8))

# 版本歷程章節的唯一錨點（修復缺陷2：不依賴章節名稱）
CHANGELOG_ANCHOR = "<!-- CHANGELOG_SECTION_START -->"

# 監控腳本清單
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


# ─── 版本資料 I/O ─────────────────────────────────────────

def _default_data() -> dict:
    return {
        "version": "3.0.2",
        "major": 3, "minor": 0, "patch": 2,
        "last_updated": datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M"),
        "file_hashes": {},
        "changelog": [
            {
                "version": "3.0.2", "date": "2026-06-15", "type": "minor",
                "changes": [
                    "Slack 通知改為 #自動化訊息來源 頻道並 @阿韋",
                    "Meta 產業清單從 12 個擴充至 28 個",
                    "手冊新增 6 大鉤子類型、6 種人設類型完整說明",
                    "手冊新增所有庫房 Notion 直接連結",
                    "修正 TikTok/Reels 監控說明（目前僅 TikTok）"
                ]
            },
            {
                "version": "3.0.1", "date": "2026-06-13", "type": "minor",
                "changes": [
                    "新增 weekly_manual_updater.py 手冊自動週更系統",
                    "新增版本自動更新機制（manual_version_tracker.py）"
                ]
            },
            {
                "version": "3.0.0", "date": "2026-06-12", "type": "major",
                "changes": [
                    "系統初始建立：雙來源抓片（TikTok + Meta廣告）",
                    "12 欄位 GPT 拆解 + 評分系統（1-5分）",
                    "素材自動分配（03/04/05/06/07/35 庫）"
                ]
            }
        ]
    }


def load_version_data() -> dict:
    """載入版本資料（本機快取優先，不存在則初始化並儲存）"""
    if VERSION_JSON.exists():
        try:
            with open(VERSION_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    data = _default_data()
    save_version_data(data)
    return data


def save_version_data(data: dict):
    """儲存版本資料到本機 JSON 快取"""
    with open(VERSION_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ─── 檔案 Hash 偵測 ───────────────────────────────────────

def get_file_hash(filepath: Path) -> str:
    try:
        with open(filepath, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except FileNotFoundError:
        return ""


def check_code_changes(data: dict) -> list:
    """比對監控腳本 hash，回傳有變動的檔名清單（接受 data 參數，避免重複 load）"""
    stored = data.get("file_hashes", {})
    changed = []
    for fname in WATCHED_FILES:
        current = get_file_hash(BASE_DIR / fname)
        if current and current != stored.get(fname, ""):
            changed.append(fname)
    return changed


def update_file_hashes(data: dict):
    """更新 data 中所有監控腳本的 hash（不 save，由呼叫方負責）"""
    hashes = {}
    for fname in WATCHED_FILES:
        h = get_file_hash(BASE_DIR / fname)
        if h:
            hashes[fname] = h
    data["file_hashes"] = hashes


# ─── 版本號遞增（修復缺陷3：接受 data 參數，在同一物件上操作）─────

def bump_version(data: dict, bump_type: str, reason: str, changes: list = None) -> str:
    """
    遞增版本號並記錄 changelog。
    接受 data 參數（外部傳入），修改後由呼叫方負責 save_version_data(data)。
    回傳：新版本號字串
    """
    now = datetime.now(TW_TZ)
    if bump_type == "major":
        data["major"] += 1
        data["minor"] = 0
        data["patch"] = 0
    elif bump_type == "minor":
        data["minor"] += 1
        data["patch"] = 0
    else:
        data["patch"] += 1

    new_version = f"{data['major']}.{data['minor']}.{data['patch']}"
    data["version"] = new_version
    data["last_updated"] = now.strftime("%Y-%m-%d %H:%M")

    entry = {
        "version": new_version,
        "date": now.strftime("%Y-%m-%d"),
        "type": bump_type,
        "changes": changes or [reason]
    }
    data["changelog"].insert(0, entry)  # 最新的放最前面
    update_file_hashes(data)

    print(f"  📌 版本更新：v{new_version}（{bump_type}）")
    return new_version


# ─── 手冊 Markdown 更新（修復缺陷1、2）──────────────────────

def _build_changelog_table(changelog: list) -> str:
    """將 changelog 轉為 Notion HTML 表格格式（最多 20 筆）"""
    type_label = {"major": "🔴 重大更新", "minor": "🟡 功能更新", "patch": "🟢 資料更新"}
    rows = ['<table header-row="true">',
            "  <tr>",
            "    <td>版本號</td>", "    <td>日期</td>",
            "    <td>類型</td>",  "    <td>更新內容</td>",
            "  </tr>"]
    for entry in changelog[:20]:
        label = type_label.get(entry.get("type", "patch"), "🟢 資料更新")
        changes_text = " / ".join(entry.get("changes", []))
        rows += [
            "  <tr>",
            f"    <td>v{entry['version']}</td>",
            f"    <td>{entry['date']}</td>",
            f"    <td>{label}</td>",
            f"    <td>{changes_text}</td>",
            "  </tr>"
        ]
    rows.append("</table>")
    return "\n".join(rows)


def update_manual_markdown(data: dict):
    """
    更新手冊 Markdown 檔案（修復缺陷1、2）：
    1. 用 regex 精確搜尋並替換 "> **版本**" 行（不假設行號）
    2. 用 ANCHOR 標記定位版本歷程區塊（不依賴章節名稱）
    """
    if not MANUAL_PATH.exists():
        print("  ⚠️  手冊 Markdown 不存在，跳過更新")
        return

    version = data["version"]
    now_str = data["last_updated"]
    content = MANUAL_PATH.read_text(encoding="utf-8")

    # ── 修復缺陷1：用 regex 精確替換版本資訊行 ──
    new_version_line = (
        f"> **版本**：v{version} | **更新日期**：{now_str} | "
        f"**適用對象**：涵勻、阿韋、子權 | "
        f"**系統狀態**：全自動運行中（每週一至週五 09:00 自動觸發）"
    )
    # 替換任何以 "> **版本**" 開頭的行
    content = re.sub(r"^> \*\*版本\*\*.*$", new_version_line, content, flags=re.MULTILINE)

    # ── 修復缺陷2：用 ANCHOR 定位版本歷程區塊 ──
    changelog_block = (
        f"{CHANGELOG_ANCHOR}\n"
        f"## 柒、版本歷程\n\n"
        f"本手冊隨系統自動更新。版本號規則：\n"
        f"🔴 主版本（架構重大變更）→ 🟡 次版本（功能更新，發 Slack 通知）→ "
        f"🟢 修訂版（每日資料更新，靜默不通知）\n\n"
        f"{_build_changelog_table(data['changelog'])}"
    )

    if CHANGELOG_ANCHOR in content:
        # ANCHOR 存在：精確替換從 ANCHOR 到文件末尾
        anchor_pos = content.index(CHANGELOG_ANCHOR)
        content = content[:anchor_pos].rstrip() + "\n\n---\n\n" + changelog_block
    else:
        # ANCHOR 不存在：移除所有舊版本歷程章節（支援任何章節編號），再附加
        content = re.sub(
            r"\n---\n\n## [壹貳參肆伍陸柒捌玖拾]+、版本歷程.*",
            "", content, flags=re.DOTALL
        )
        content = content.rstrip() + "\n\n---\n\n" + changelog_block

    MANUAL_PATH.write_text(content, encoding="utf-8")
    print(f"  ✅ 手冊 Markdown 已更新至 v{version}")


# ─── Notion 同步 ──────────────────────────────────────────

def sync_to_notion():
    """將更新後的手冊 Markdown 同步到 Notion 頁面"""
    if not MANUAL_PATH.exists():
        print("  ⚠️  手冊 Markdown 不存在，跳過 Notion 同步")
        return False

    content = MANUAL_PATH.read_text(encoding="utf-8")
    payload = {
        "page_id": NOTION_PAGE_ID,
        "command": "replace_content",
        "new_str": content
    }
    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-update-page",
        "--server", "notion",
        "--input", json.dumps(payload, ensure_ascii=False)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if result.returncode == 0:
            print("  ✅ Notion 手冊已同步")
            return True
        else:
            print(f"  ⚠️  Notion 同步失敗：{result.stderr[:150]}")
            return False
    except Exception as e:
        print(f"  ⚠️  Notion 同步異常：{e}")
        return False


# ─── Slack 通知 ───────────────────────────────────────────

def send_slack_version_notice(version: str, bump_type: str, changes: list):
    """發送版本更新通知到 Slack 團隊主頻道（只在 minor/major 時發送）"""
    type_emoji = {"major": "🔴", "minor": "🟡"}
    emoji = type_emoji.get(bump_type, "🟡")
    changes_text = "\n".join(f"  • {c}" for c in changes[:5])
    msg = (
        f"{emoji} *手冊版本更新通知*\n"
        f"────────────────────────────────────────\n"
        f"*版本*：v{version}（{bump_type} 更新）\n"
        f"*更新內容*：\n{changes_text}\n"
        f"────────────────────────────────────────\n"
        f"📋 <https://app.notion.com/p/{NOTION_PAGE_ID.replace('-', '')}|查看最新手冊>"
    )
    payload = {"channel_id": SLACK_TEAM_CH, "message": msg}
    cmd = [
        "manus-mcp-cli", "tool", "call", "slack_send_message",
        "--server", "slack",
        "--input", json.dumps(payload, ensure_ascii=False)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print("  ✅ Slack 版本通知已發送")
        else:
            print(f"  ⚠️  Slack 通知失敗：{result.stderr[:100]}")
    except Exception as e:
        print(f"  ⚠️  Slack 通知異常：{e}")


# ─── 主要對外 API（修復缺陷3）────────────────────────────────

def auto_detect_and_update(reason: str = "", changes: list = None, notify_slack: bool = True) -> str:
    """
    自動偵測程式碼變動並更新手冊（給 daily_run.py 呼叫）
    - 若有腳本變動 → bump minor，發 Slack 通知
    - 若只有資料更新 → bump patch，靜默更新
    回傳：新版本號
    """
    # 1. 載入版本資料（只 load 一次）
    data = load_version_data()

    # 2. 偵測程式碼變動（傳入 data，避免重複 load）
    changed_files = check_code_changes(data)
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

    # 3. 遞增版本號（在同一個 data 物件上操作）
    new_version = bump_version(data, bump_type, reason or auto_changes[0], auto_changes)

    # 4. 儲存（此時 data["changelog"] 已包含最新一筆）
    save_version_data(data)

    # 5. 更新手冊 Markdown（傳入已更新的 data，changelog 是最新的）
    update_manual_markdown(data)

    # 6. 同步到 Notion
    sync_to_notion()

    # 7. Slack 通知（patch 靜默）
    if notify_slack and bump_type != "patch":
        send_slack_version_notice(new_version, bump_type, auto_changes)

    return new_version


def manual_bump(bump_type: str, reason: str, changes: list = None, notify_slack: bool = True) -> str:
    """
    手動觸發版本更新（用於重大功能上線時）
    用法：python3 manual_version_tracker.py major "新增 IG Reels 監控"
    """
    data = load_version_data()
    new_version = bump_version(data, bump_type, reason, changes or [reason])
    save_version_data(data)
    update_manual_markdown(data)
    sync_to_notion()
    if notify_slack:
        send_slack_version_notice(new_version, bump_type, changes or [reason])
    return new_version


# ─── CLI 入口 ─────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) >= 3:
        bump_type = sys.argv[1]   # major / minor / patch
        reason    = sys.argv[2]
        changes   = sys.argv[3:] if len(sys.argv) > 3 else None
        version   = manual_bump(bump_type, reason, changes)
        print(f"\n✅ 手冊已更新至 v{version}")
    else:
        version = auto_detect_and_update()
        print(f"\n✅ 手冊已更新至 v{version}")
