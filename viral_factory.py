"""爆款短影音拆解工廠 v3.0
好創整合行銷 | 子權 2026-06-10

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

# ─── GPT-4o 拆解 Prompt（v3.0 頂尖方法論版）────────────────
# 基於視覺錘×語言釘×鉤子設計系統×Meta廣告投放邏輯建立
ANALYSIS_PROMPT = """
你是「好創整合行銷」的頂尖短影音爆款拆解師，精通以下知識體系：
- 視覺錘（Visual Hammer）× 語言釘（Verbal Nail）品牌定位理論
- 五大鉤子類型：大膽宣言型、問題型、前後對比型、好奇缺口型、痛點激化型
- Meta Andromeda 演算法（2026）：素材 > 受眾，3秒留存率決定推播
- 各產業最佳廣告素材結構（餐飲/美容/電商/服務業）
- 人設定位系統：專家型/素人見證型/創業者型/在地達人型/生活達人型/挑戰者型

你的任務：對這支影片進行「頂尖拆解」，目標是讓閱讀者能直接複製這套方法論。
每個欄位都要具體、有深度，給出「可直接套用的洞察」，不要空泛描述。

請以 JSON 格式回傳，包含以下 12 個欄位：

{{
  "影片標題或主題": "用一句話描述核心主題（20字以內）",
  
  "鉤子類型與設計": {{
    "鉤子類型": "五大類型之一：大膽宣言型/問題型/前後對比型/好奇缺口型/痛點激化型",
    "前3秒設計": "具體描述前3秒的視覺+聽覺+文字設計，為什麼能讓人停下來",
    "心理機制": "這個鉤子觸發了什麼心理機制（認知衝突/好奇缺口/社會證明/情緒共鳴）",
    "可套用公式": "把這個鉤子抽象成可複製的公式，例如：[痛點場景]+[意外結果]+[懸念]"
  }},
  
  "視覺錘分析": {{
    "視覺錘是什麼": "這支影片用了什麼視覺符號讓人記住品牌/內容？",
    "語言釘是什麼": "這支影片的核心一句話定位是什麼？",
    "視覺錘強度": "強/中/弱，並說明原因"
  }},
  
  "人設定位分析": {{
    "人設類型": "六種之一：專家型/素人見證型/創業者型/在地達人型/生活達人型/挑戰者型",
    "人設如何建立信任": "具體說明這個人設如何讓觀眾信任並繼續看",
    "可複製性": "這個人設好創客戶能複製嗎？如何複製？"
  }},
  
  "影片結構拆解": {{
    "敘事框架": "PAS/AIDA/Before-After/問題-解方-行動/其他，說明框架邏輯",
    "各段功能": "0-3秒做什麼、3-30秒做什麼、30秒後做什麼，每段的留存機制",
    "節奏設計": "剪輯節奏、轉折點、中段小鉤子設計"
  }},
  
  "視覺設計亮點": {{
    "畫面構圖": "特寫/遠景/對比/動態等，為什麼這樣的構圖有效",
    "字幕設計": "字幕風格、出現時機、強調方式",
    "靜音可懂度": "關掉聲音只看畫面，能理解核心訊息嗎？（是/否，說明原因）"
  }},
  
  "CTA設計分析": {{
    "CTA類型": "購買/留言/分享/收藏/點擊連結/其他",
    "降低行動門檻的手法": "用了什麼方法讓觀眾更容易採取行動？",
    "CTA強度評分": "1-10分，說明原因"
  }},
  
  "爆款原因深度分析": {{
    "演算法層面": "這支影片在3秒留存率/完播率/互動密度上的優勢是什麼？",
    "心理學層面": "觸發了哪些心理機制（好奇/恐懼/社會比較/從眾/稀缺感）？",
    "受眾共鳴層面": "精準打到哪個受眾的什麼痛點/慾望/身份認同？",
    "可複製洞察": "這支影片最值得好創客戶複製的1-3個核心洞察，條列式"
  }},
  
  "廣告投放潛力評估": {{
    "適合投廣告嗎": "是/否/需修改，說明原因",
    "最適合的廣告目標": "品牌知名度/流量/名單/轉換，說明為什麼",
    "預估Hook Rate": "高/中/低，依據前3秒設計判斷",
    "需要修改什麼才能投廣告": "如果需要修改，具體說明"
  }},
  
  "產業適用性分析": {{
    "原始產業": "這支影片屬於哪個產業？",
    "可移植到哪些產業": "這個鉤子/結構/人設可以移植到哪些產業？怎麼移植？",
    "好創客戶適用性": "好創服務的餐飲/電商/服務業客戶，哪個最適合借鏡這支影片？"
  }},
  
  "無效因素識別": [
    "列出這支影片的弱點或無效元素，例如：開頭太慢/CTA太弱/靜音無法理解等"
  ],
  
  "熱門音樂": "音樂風格、情緒功能、為什麼選這首（若無法判斷填：需人工補充）",
  
  "類別標籤": ["從以下選項中選擇最符合的（可多選）：餐飲、婚禮、家具、知識、搞笑、劇情、教學、美容、電商、服務業、廣告素材、其他"]
}}

逐字稿如下：
{transcript}

影片平台：{platform}
影片連結：{url}

重要提醒：
1. 每個欄位都要給出「可直接套用的洞察」，不要空泛描述
2. 「可套用公式」欄位是最重要的，要讓人看完就能複製
3. 如果逐字稿不足以判斷某些視覺元素，請標注「需人工補充」
4. 用台灣繁體中文，語氣專業但口語化
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
    def fmt(val):
        if isinstance(val, dict):
            return '\n'.join(f'- **{k}**：{v}' for k, v in val.items())
        if isinstance(val, list):
            return '\n'.join(f'- {item}' for item in val)
        return str(val)

    hook = analysis.get('鉤子類型與設計', {})
    visual_hammer = analysis.get('視覺錘分析', {})
    persona = analysis.get('人設定位分析', {})
    structure = analysis.get('影片結構拆解', {})
    visual_design = analysis.get('視覺設計亮點', {})
    cta = analysis.get('CTA設計分析', {})
    boom_reason = analysis.get('爆款原因深度分析', {})
    ad_potential = analysis.get('廣告投放潛力評估', {})
    industry = analysis.get('產業適用性分析', {})
    invalid = analysis.get('無效因素識別', [])

    content = (
        f"## 🎣 鉤子類型與設計\n\n{fmt(hook)}\n\n"
        f"## 🔨 視覺錘 × 語言釘\n\n{fmt(visual_hammer)}\n\n"
        f"## 🎭 人設定位分析\n\n{fmt(persona)}\n\n"
        f"## 📐 影片結構拆解\n\n{fmt(structure)}\n\n"
        f"## 🎬 視覺設計亮點\n\n{fmt(visual_design)}\n\n"
        f"## 📣 CTA設計分析\n\n{fmt(cta)}\n\n"
        f"## 💥 爆款原因深度分析\n\n{fmt(boom_reason)}\n\n"
        f"## 📊 廣告投放潛力評估\n\n{fmt(ad_potential)}\n\n"
        f"## 🏭 產業適用性分析\n\n{fmt(industry)}\n\n"
        f"## ⚠️ 無效因素識別\n\n{fmt(invalid)}\n\n"
        f"## 📝 逐字稿\n\n{transcript[:2000]}"
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
                f"*鉤子類型：* {analysis.get('鉤子類型與設計', {}).get('鉤子類型', '') if isinstance(analysis.get('鉤子類型與設計'), dict) else ''}\n"
                f"*可套用公式：* {analysis.get('鉤子類型與設計', {}).get('可套用公式', '') if isinstance(analysis.get('鉤子類型與設計'), dict) else ''}\n"
                f"*廣告潛力：* {analysis.get('廣告投放潛力評估', {}).get('適合投廣告嗎', '') if isinstance(analysis.get('廣告投放潛力評估'), dict) else ''}\n"
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
