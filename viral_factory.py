"""
爆款短影音拆解工廠 v2.0
好創整合行銷 | 子權 2026-06-08

流程：影片連結 → yt-dlp 下載 → Whisper 轉文字 → GPT-4o 拆解 → Notion MCP 寫入 → Slack MCP 通知
架構：Whisper 用子權的 OpenAI Key（直接呼叫 api.openai.com）
      GPT-4o 用沙盒免費代理（api.manus.im）
      Notion / Slack 用 MCP 工具（不需要額外 Key）
"""

import os
import json
import tempfile
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from openai import OpenAI

# ─── 設定區 ───────────────────────────────────────────────
# Whisper 專用（直接呼叫 OpenAI 原始 API）
WHISPER_API_KEY = os.environ.get(
    "WHISPER_API_KEY",
    "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"
)

# Notion 資料庫 ID（02 爆款拆解庫）
NOTION_DB_ID = "82097a06-fae5-83bd-a8c3-87236d3713aa"

# Slack 設定
SLACK_AWEI_ID    = "U0B4FG0ER89"   # 阿韋 DM
SLACK_TEAM_CH    = "C0AQG307XJT"   # #all-團隊主頻道

# ─── GPT-4o 拆解 Prompt ───────────────────────────────────
ANALYSIS_PROMPT = """
你是一位專業的短影音爆款拆解師，服務台灣整合行銷公司「好創整合行銷」。

請根據以下影片逐字稿，對這支影片進行完整的爆款結構拆解。
請以 JSON 格式回傳，包含以下 8 個欄位，每個欄位都要具體、有深度，不要空泛：

{{
  "影片標題或主題": "用一句話描述這支影片的核心主題（20字以內）",
  "開頭鉤子拆解": "前3秒用了什麼鉤子手法？心理機制是什麼？具體說明",
  "結構拆解": "完整的敘事框架（例如：PAS/AIDA/Before-After），每個段落的功能與邏輯",
  "視覺亮點": "畫面設計、字幕風格、特效、剪輯節奏的關鍵亮點",
  "結尾呼籲拆解": "CTA 是什麼？用了什麼心理機制降低行動門檻？",
  "為什麼會爆": "從心理學、演算法、受眾共鳴三個角度分析爆款原因，至少5點，條列式",
  "熱門音樂": "音樂風格與情緒功能描述（若逐字稿無法判斷，填「需人工補充」）",
  "類別標籤": ["從以下選項中選擇最符合的（可多選）：餐飲、婚禮、家具、知識、搞笑、劇情、教學、其他"]
}}

逐字稿如下：
{transcript}

影片平台：{platform}
影片連結：{url}
"""

# ─── 工具函數 ─────────────────────────────────────────────

def detect_platform(url: str) -> str:
    url_lower = url.lower()
    if "tiktok.com" in url_lower:
        return "TikTok"
    elif "instagram.com" in url_lower:
        return "Reels"
    elif "facebook.com" in url_lower or "fb.watch" in url_lower:
        return "FB"
    elif "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "Shorts"
    elif "douyin.com" in url_lower:
        return "抖音"
    elif "xiaohongshu.com" in url_lower:
        return "小紅書"
    return "其他"


def download_video(url: str, output_dir: str) -> str:
    """用 yt-dlp 下載影片音頻，回傳本地檔案路徑"""
    output_template = os.path.join(output_dir, "%(id)s.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--format", "bestaudio/best",
        "--output", output_template,
        "--no-warnings",
        url
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp 失敗：{result.stderr[:300]}")

    files = sorted(Path(output_dir).glob("*"), key=lambda f: f.stat().st_mtime)
    if not files:
        raise RuntimeError("yt-dlp 下載後找不到檔案")
    return str(files[-1])


def transcribe_audio(audio_path: str) -> str:
    """用 Whisper API 轉錄音頻（直接呼叫 OpenAI 原始 API）"""
    client = OpenAI(
        api_key=WHISPER_API_KEY,
        base_url="https://api.openai.com/v1"
    )
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            language="zh"
        )
    return result.text


def analyze_with_gpt4o(transcript: str, platform: str, url: str) -> dict:
    """用 GPT-4o 拆解（走沙盒免費代理）"""
    client = OpenAI()  # 使用沙盒預設環境變數
    prompt = ANALYSIS_PROMPT.format(
        transcript=transcript[:3000],
        platform=platform,
        url=url
    )
    response = client.chat.completions.create(
        model="gpt-4.1-mini",  # 沙盒可用模型
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.3
    )
    # 沙盒代理在模型不支援時會回傳 error 欄位而非 choices
    if response.choices is None:
        err = getattr(response, 'error', 'Unknown error')
        raise RuntimeError(f"GPT API 錯誤：{err}")
    return json.loads(response.choices[0].message.content)


def check_duplicate_via_mcp(url: str) -> bool:
    """透過 Notion MCP 查詢是否已有此連結"""
    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-query-database",
        "--server", "notion",
        "--input", json.dumps({
            "data_source_id": NOTION_DB_ID,
            "filter": {
                "property": "原始連結",
                "url": {"equals": url}
            }
        })
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return False
    try:
        data = json.loads(result.stdout.split("Tool execution result:\n")[-1])
        return len(data.get("results", [])) > 0
    except Exception:
        return False


def write_to_notion_via_mcp(url: str, platform: str, transcript: str, analysis: dict) -> str:
    """透過 Notion MCP 寫入爆款拆解庫，回傳頁面 URL"""
    tags = analysis.get("類別標籤", ["其他"])
    if isinstance(tags, str):
        tags = [tags]
    valid_tags = ["餐飲", "婚禮", "家具", "知識", "搞笑", "劇情", "教學", "其他"]
    tags = [t for t in tags if t in valid_tags] or ["其他"]
    tags_json = json.dumps(tags, ensure_ascii=False)

    valid_platforms = ["TikTok", "Reels", "Shorts", "FB", "抖音", "小紅書", "其他"]
    if platform not in valid_platforms:
        platform = "其他"

    title = analysis.get("影片標題或主題", "未命名影片")[:100]

    # 組合頁面內容（Notion Markdown 格式）
    why_boom = analysis.get('為什麼會爆', '')
    if isinstance(why_boom, list):
        why_boom = '\n'.join(f'- {item}' for item in why_boom)

    content = (
        f"## 開頭鉤子拆解\n\n{analysis.get('開頭鉤子拆解', '')}\n\n"
        f"## 結構拆解\n\n{analysis.get('結構拆解', '')}\n\n"
        f"## 視覺亮點\n\n{analysis.get('視覺亮點', '')}\n\n"
        f"## 結尾呼籲拆解\n\n{analysis.get('結尾呼籲拆解', '')}\n\n"
        f"## 為什麼會爆\n\n{why_boom}\n\n"
        f"## 逐字稿\n\n{transcript[:2000]}"
    )

    payload = {
        "parent": {"data_source_id": NOTION_DB_ID},
        "pages": [
            {
                "icon": "🔥",
                "content": content,
                "properties": {
                    "影片標題或主題": title,
                    "原始連結": url,
                    "平台": platform,
                    "爆款數據": analysis.get("爆款數據", "待補充"),
                    "熱門音樂": analysis.get("熱門音樂", "需人工補充"),
                    "是否已借鏡": "__NO__",
                    "類別標籤": tags_json,
                    "入庫日期": datetime.now().strftime("%Y-%m-%d")
                }
            }
        ]
    }

    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-create-pages",
        "--server", "notion",
        "--input", json.dumps(payload, ensure_ascii=False)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

    if result.returncode != 0:
        raise RuntimeError(f"Notion 寫入失敗：{result.stderr[:200]}")

    # 嘗試解析回傳的頁面 URL
    try:
        output = result.stdout.split("Tool execution result:\n")[-1].strip()
        data = json.loads(output)
        pages = data.get("pages", [])
        return pages[0].get("url", "https://notion.so") if pages else "https://notion.so"
    except Exception:
        return "https://notion.so"


def send_slack_dm(message: str, channel: str = None):
    """透過 Slack MCP 發送訊息"""
    target = channel or SLACK_AWEI_ID
    cmd = [
        "manus-mcp-cli", "tool", "call", "slack_send_message",
        "--server", "slack",
        "--input", json.dumps({
            "channel_id": target,
            "message": message
        })
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"  ⚠️  Slack 發送失敗：{result.stderr[:100]}")


# ─── 主流程 ───────────────────────────────────────────────

def process_single_video(url: str, whisper_available: bool = True) -> dict:
    """
    處理單支影片的完整拆解流程
    whisper_available: False 時跳過轉錄，使用模擬逐字稿（測試用）
    """
    platform = detect_platform(url)
    print(f"\n{'='*55}")
    print(f"開始拆解：{url[:60]}")
    print(f"平台：{platform}")
    print(f"{'='*55}")

    try:
        # 去重檢查
        print(f"  [0/5] 去重檢查...")
        if check_duplicate_via_mcp(url):
            print(f"  ⏭️  已存在，跳過")
            return {"success": False, "error": "duplicate", "url": url}

        with tempfile.TemporaryDirectory() as tmpdir:
            # Step 1: 下載
            print(f"  [1/5] 下載影片...")
            audio_path = download_video(url, tmpdir)
            size_kb = Path(audio_path).stat().st_size / 1024
            print(f"  下載完成：{Path(audio_path).name}（{size_kb:.0f} KB）")

            # Step 2: 轉文字
            if whisper_available:
                print(f"  [2/5] Whisper 語音轉文字...")
                transcript = transcribe_audio(audio_path)
                if not transcript.strip():
                    transcript = "（無語音內容，純視覺影片）"
                print(f"  轉錄完成：{len(transcript)} 字")
            else:
                transcript = "（Whisper API 額度不足，逐字稿待補充）"
                print(f"  [2/5] 跳過轉錄（API 額度不足）")

            # Step 3: AI 拆解
            print(f"  [3/5] GPT 拆解分析...")
            analysis = analyze_with_gpt4o(transcript, platform, url)
            print(f"  拆解完成：{analysis.get('影片標題或主題', '')[:30]}")

            # Step 4: 寫入 Notion
            print(f"  [4/5] 寫入 Notion...")
            notion_url = write_to_notion_via_mcp(url, platform, transcript, analysis)
            print(f"  寫入完成：{notion_url}")

            # Step 5: Slack 通知
            print(f"  [5/5] Slack 通知阿韋...")
            today = datetime.now().strftime("%Y-%m-%d")
            msg = (
                f"🔥 *爆款入庫* | {today}\n\n"
                f"*平台：* {platform}\n"
                f"*標題：* {analysis.get('影片標題或主題', '未命名')}\n"
                f"*開頭鉤子：* {analysis.get('開頭鉤子拆解', '')[:80]}...\n"
                f"*Notion：* {notion_url}"
            )
            send_slack_dm(msg)
            print(f"  通知已發送")

            print(f"\n  ✅ 完成！")
            return {
                "success": True,
                "notion_url": notion_url,
                "title": analysis.get("影片標題或主題", ""),
                "platform": platform,
                "url": url,
                "analysis": analysis
            }

    except Exception as e:
        print(f"\n  ❌ 失敗：{e}")
        return {"success": False, "error": str(e), "url": url}


def process_batch(urls: list, whisper_available: bool = True) -> list:
    """批次處理多支影片"""
    results = []
    print(f"\n🏭 爆款短影音拆解工廠啟動")
    print(f"本次任務：{len(urls)} 支影片")
    print(f"時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}")

    for i, url in enumerate(urls, 1):
        print(f"\n[{i}/{len(urls)}]", end="")
        result = process_single_video(url, whisper_available)
        results.append(result)

    # 發送日報
    success_items = [r for r in results if r.get("success")]
    fail_count = len(results) - len(success_items)
    today = datetime.now().strftime("%Y-%m-%d")

    lines = [f"📊 *爆款入庫日報* | {today}\n"]
    lines.append(f"今日入庫：*{len(success_items)} 支* | 失敗/跳過：{fail_count} 支\n")
    for i, r in enumerate(success_items, 1):
        lines.append(f"{i}. [{r.get('platform','')}] {r.get('title','')}")
        lines.append(f"   → {r.get('notion_url','')}")

    send_slack_dm("\n".join(lines))

    print(f"\n{'='*55}")
    print(f"✅ 完成：{len(success_items)} 支 | ❌ 失敗：{fail_count} 支")
    return results


# ─── 執行入口 ─────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python3 viral_factory.py <影片連結> [連結2] ...")
        sys.exit(1)

    urls = sys.argv[1:]
    whisper_ok = True  # 若 Whisper 額度不足，改為 False

    if len(urls) == 1:
        process_single_video(urls[0], whisper_ok)
    else:
        process_batch(urls, whisper_ok)
