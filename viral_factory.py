"""爆款短影音拆解工廠 v3.0
好創整合行銷 | 子權 2026-06-10

流程：影片連結 → yt-dlp 下載 → manus-speech-to-text 轉文字 → GPT（gpt-4.1-mini）拆解 → Notion MCP 寫入 → Slack MCP 通知
架構：轉錄用 manus-speech-to-text（沙盒內建工具，不需要 OpenAI Key）
      GPT（gpt-4.1-mini）用沙盒免費代理（api.manus.im）
      Notion / Slack 用 MCP 工具（不需要額外 Key）
"""

import os
import json
import re
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from execution_tracker import log_delivery
from pathlib import Path

import fcntl
from openai import OpenAI
from script_scorer import score_video

from data_quality import (
    EVIDENCE_INSUFFICIENT,
    OUTCOME_DUPLICATE,
    OUTCOME_QUEUED,
    OUTCOME_QUARANTINED,
    OUTCOME_TECHNICAL_ERROR,
    OUTCOME_UNIQUE_SUCCESS,
    OUTCOME_WRITE_UNVERIFIED,
    canonical_video_identity,
    claim_video,
    evaluate_evidence,
    mark_locally_processed,
    may_publish_guidance,
    optional_int,
    release_video_claim,
    sanitize_unverifiable_analysis,
)

# ─── 設定區 ───────────────────────────────────────────────
# Whisper 專用（直接呼叫 OpenAI 原始 API）
WHISPER_API_KEY = os.environ.get(
    "WHISPER_API_KEY",
    os.environ.get("OPENAI_API_KEY", "")
)

# 動態計算路徑，避免硬編碼 /home/ubuntu 導致環境移植失敗
BASE_DIR = Path(__file__).resolve().parent

# Notion 資料庫 ID（02 爆款拆解庫）
NOTION_DB_ID = os.environ.get("NOTION_DB_MAIN", "82097a06-fae5-83bd-a8c3-87236d3713aa")
PROCESSED_REGISTRY = BASE_DIR / "data" / "processed_videos.json"
# P0 safety default: until a real visual-evidence stage is implemented and
# validated, the scheduled job may collect evidence but cannot publish AI
# guidance or create reusable knowledge assets.
VIRAL_SAFE_MODE = os.environ.get("VIRAL_SAFE_MODE", "true").lower() != "false"

# Slack 設定（從 .env 讀取，人員異動時只需改 .env 不需動程式碼）
# .env 路徑：<專案目錄>/.env
SLACK_AWEI_ID    = os.environ.get("SLACK_AWEI_ID",    "U0B4FG0ER89")   # 阿韋 User ID
SLACK_XINXIN_ID  = os.environ.get("SLACK_XINXIN_ID",  "U0BA2DKQ7GF")   # 小鑫 User ID
SLACK_TEAM_CH    = os.environ.get("SLACK_TEAM_CH",    "C0AQG307XJT")   # #all-團隊主頻道
SLACK_AUTO_CH    = os.environ.get("SLACK_AUTO_CH",    "C0AUH4QKF5M")   # #自動化訊息來源（影音類別）

# ─── GPT-4o 拆解 Prompt（v4.0 全類型分角色版）────────────────
# 新增：影片類型分類（6種）、企劃師版輸出、剪輯師版輸出、爆款原因三層分析
ANALYSIS_PROMPT = """
你是「好創整合行銷」的頂尖短影音爆款拆解師，精通以下知識體系：
- 視覺錘（Visual Hammer）× 語言釘（Verbal Nail）品牌定位理論
- 六大鉤子類型：大膽宣言型、問題型、前後對比型、好奇缺口型、痛點激化型、挑戰型
- Meta Andromeda 演算法（2026）：素材 > 受眾，3秒留存率決定推播
- 各產業最佳廣告素材結構（餐飲/美容/電商/服務業）
- 人設定位系統：專家型/素人見證型/創業者型/在地達人型/生活達人型/挑戰者型
- 六大影片類型：導購型/個人IP型/品牌IP型/UGC型/時事議題型/病毒式傳播型

你的任務：對這支影片進行「頂尖拆解」，同時產出「企劃師版」與「剪輯師版」兩份可直接使用的應用建議。
每個欄位都要具體、有深度，給出「可直接套用的洞察」，不要空泛描述。

請以 JSON 格式回傳，包含以下欄位：

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

  "影片類型": "必須從以下選項選一個：導購型、個人IP型、品牌IP型、UGC型、時事議題型、病毒式傳播型",

  "為什麼會爆款": "用3句話以內說明：這支影片為什麼會成為爆款？核心原因是什麼？",

  "為什麼是好影片": "用3句話以內說明：從影片製作品質、內容價值、觀眾體驗三個角度，說明這是一部好影片的原因",

  "能達到什麼效果": "這支影片能達到什麼商業或傳播效果？例如：品牌認知提升/直接帶貨/粉絲增長/話題擴散等",

  "企劃師應用建議": {{
    "本週可用選題": "根據這支影片，企劃師本週可以發展的1個具體選題方向（含標題公式）",
    "可套用的開場白公式": "把這支影片的開場白抽象成可填空的公式，例如：[痛點]+[意外轉折]+[懸念]",
    "適合哪類客戶": "好創目前服務的哪類客戶最適合借鑑這支影片？為什麼？",
    "改編建議": "如果要為台灣本土品牌改編這支影片，最需要調整的3個地方是什麼？"
  }},

  "剪輯師應用建議": {{
    "前3秒剪輯指令": "具體說明前3秒要怎麼剪：鏡頭切換方式、字幕出現時機、音效使用",
    "節奏時間軸": "用時間點描述整支影片的剪輯節奏，例如：0-3秒快切3個鏡頭→3-15秒平穩敘事→15-25秒衝突高潮→25秒後CTA",
    "視覺錘強調方式": "剪輯時如何強調這支影片的視覺錘？用什麼特效或字幕設計？",
    "音效與音樂建議": "這支影片的音效節奏如何配合剪輯？哪些時間點需要特別的音效強調？",
    "熱門音樂趨勢": "根據當前市場（TikTok/Meta）判斷這類影片適合搭配的音樂風格、情緒功能，並說明為什麼",
    "剪輯技巧建議": "針對這支影片的畫面，給出 3 個具體的剪輯技巧建議（例如：畫面放大縮小、特定轉場、關鍵字字幕強調位置）"
  }},

  "熱門音樂": "音樂風格、情緒功能、為什麼選這首（若無法判斷填：需人工補充）",
  
  "類別標籤": ["從以下選項中選擇最符合的（可多選，必須從此清單選擇）：美妝保養、餐飲食品、服飾配件、保健醫療、數位課程、服務業、家居家具、婚禮攝影、3C電子、通用"],

  "鉤子大類": "必須從以下選項選一個：疑問式、否定式、衝突式、數字式、警告式、揭秘式、前後對比式、大膽宣言式",

  "視覺錘類型": "必須從以下選項選一個：道具型、構圖型、色彩型、字幕型、人物型、場景型、動作型",

  "CTA類型": "必須從以下選項選一個：留言誘餌、私訊獲取、追蹤鉤子、到店導流、加LINE、預約、點連結購買、填表單",

  "神經科學機制": "必須從以下選項選一個（最主要的那個）：杏仁核劫持、多巴胺預期、鏡像神經元、認知失調、損失厭惡、社交認同",

  "廣告投放潛力": "必須從以下選項選一個：A級直接可投、B級小改可投、C級需大改、不適合投廣告"
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


def fetch_video_metadata(url: str) -> dict:
    """
    用 yt-dlp --dump-json 取得影片 metadata（不下載）
    回傳 {title, description, view_count, uploader, duration}
    """
    cmd = [
        "yt-dlp",
        "--dump-json",
        "--no-playlist",
        "--no-warnings",
        "--ignore-errors",
        url
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode == 0 and result.stdout.strip():
        try:
            info = json.loads(result.stdout.strip().split("\n")[0])
            return {
                "title": info.get("title", ""),
                "description": info.get("description", ""),
                "view_count": optional_int(info.get("view_count")),
                "like_count": optional_int(info.get("like_count")),
                "uploader": info.get("uploader", ""),
                "duration": optional_int(info.get("duration")),
                "upload_date": info.get("upload_date", ""),
            }
        except Exception:
            pass
    return {}


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
        err = result.stderr[:300]
        # IP 封鎖或 403 時拋出特定錯誤，讓上層降級處理
        if "blocked" in err.lower() or "403" in err or "forbidden" in err.lower():
            raise RuntimeError(f"IP_BLOCKED:{err}")
        raise RuntimeError(f"yt-dlp 失敗：{err}")

    files = sorted(Path(output_dir).glob("*"), key=lambda f: f.stat().st_mtime)
    if not files:
        raise RuntimeError("yt-dlp 下載後找不到檔案")
    return str(files[-1])


def transcribe_audio(audio_path: str) -> str:
    """用 manus-speech-to-text 轉錄音頻（沙盒內建工具，不需要 OpenAI Key）"""
    result = subprocess.run(
        ["manus-speech-to-text", audio_path],
        capture_output=True, text=True, timeout=180
    )
    if result.returncode != 0:
        raise RuntimeError(f"manus-speech-to-text 失敗：{result.stderr.strip()}")
    output = result.stdout.strip()
    if not output:
        raise RuntimeError("manus-speech-to-text 回傳空白逐字稿")
    return output


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
        temperature=0.3,
        max_tokens=4000,  # 限制輸出長度，避免超長輸出浪費 token
    )
    # 沙盒代理在模型不支援時會回傳 error 欄位而非 choices
    if response.choices is None:
        err = getattr(response, 'error', 'Unknown error')
        raise RuntimeError(f"GPT API 錯誤：{err}")
    raw_content = response.choices[0].message.content
    # 防護：移除 GPT 偶爾輸出的 Markdown 代碼塊標記（```json ... ```）
    if raw_content.strip().startswith("```"):
        lines = raw_content.strip().split("\n")
        # 移除第一行（```json 或 ```）和最後一行（```）
        raw_content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(raw_content)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"GPT 輸出 JSON 解析失敗：{e}\n原始輸出（前200字）：{raw_content[:200]}")


def check_duplicate_via_mcp(url: str, platform_video_id: str = "") -> bool:
    """Query Notion by stable identity and canonical URL; uncertainty blocks ingestion."""
    import re as _re
    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-query-data-sources",
        "--server", "notion",
        "--input", json.dumps({
            "data_source_id": NOTION_DB_ID,
            "filter": {
                "or": [
                    {
                        "property": "platform_video_id",
                        "rich_text": {"equals": platform_video_id},
                    },
                    {
                        "property": "原始連結",
                        "url": {"equals": url},
                    },
                ]
            },
        })
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"DEDUPE_UNAVAILABLE:{result.stderr[:200]}")
    try:
        raw = result.stdout.strip()
        raw = _re.sub(r'\x1b\[[0-9;]*m', '', raw)
        if "Tool execution result:" in raw:
            raw = raw.split("Tool execution result:")[-1].strip()
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("error"):
            raise ValueError(f"unexpected payload: {str(data)[:200]}")
        if "results" not in data and "pages" not in data:
            raise ValueError("missing results/pages")
        rows = data.get("results") if "results" in data else data.get("pages")
        if not isinstance(rows, list):
            raise ValueError("results/pages is not a list")
        return len(rows) > 0
    except Exception as exc:
        raise RuntimeError(f"DEDUPE_PARSE_ERROR:{type(exc).__name__}:{exc}") from exc


def verify_notion_page_via_mcp(page_url: str, expected_source_url: str) -> bool:
    """Read the newly-created page back and verify its source URL."""
    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-fetch",
        "--server", "notion",
        "--input", json.dumps({"url": page_url}),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return False
    return expected_source_url in result.stdout


def write_to_notion_via_mcp(url: str, platform: str, transcript: str, analysis: dict) -> str:
    """透過 Notion MCP 寫入爆款拆解庫，回傳頁面 URL"""
    # 產業分類 → Notion 現有選項 對照表
    # Notion 類別標籤現有選項：餐飲/婚禮/家具/知識/搞笑/劇情/教學/其他
    # GPT 輸出的產業分類 → 對應到最接近的 Notion 選項
    INDUSTRY_TO_NOTION = {
        "美妝保養": "其他",
        "餐飲食品": "餐飲",
        "服飾配件": "其他",
        "保健醫療": "其他",
        "數位課程": "知識",
        "服務業": "其他",
        "家居家具": "家具",
        "婚禮攝影": "婚禮",
        "3C電子": "其他",
        "寵物": "其他",
        "通用": "其他",
        # Notion 原生選項直接對應
        "餐飲": "餐飲",
        "婚禮": "婚禮",
        "家具": "家具",
        "知識": "知識",
        "搞笑": "搞笑",
        "劇情": "劇情",
        "教學": "教學",
        "其他": "其他",
    }
    NOTION_VALID_TAGS = ["餐飲", "婚禮", "家具", "知識", "搞笑", "劇情", "教學", "其他"]

    raw_tags = analysis.get("類別標籤", ["其他"])
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    # 轉換產業分類為 Notion 可接受的選項
    tags = list(dict.fromkeys(
        INDUSTRY_TO_NOTION.get(t, "其他") for t in raw_tags
        if INDUSTRY_TO_NOTION.get(t, "其他") in NOTION_VALID_TAGS
    )) or ["其他"]
    # Notion MCP multi_select 只接受單一字串，取第一個最相關的標籤
    tags_json = tags[0] if tags else "其他"

    # 結構化標籤欄位（對應 Notion 選項）
    valid_hook_types = ["疑問式", "否定式", "衝突式", "數字式", "警告式", "揭秘式", "前後對比式", "大膽宣言式"]
    valid_visual_hammer = ["道具型", "構圖型", "色彩型", "字幕型", "人物型", "場景型", "動作型"]
    valid_cta_types = ["留言誘餌", "私訊獲取", "追蹤鉤子", "到店導流", "加LINE", "預約", "點連結購買", "填表單"]
    valid_neuro = ["杏仁核劫持", "多巴胺預期", "鏡像神經元", "認知失調", "損失厭惡", "社交認同"]
    # GPT 常見輸出的別名映射到 Notion 合法選項
    NEURO_ALIAS = {
        "好奇心缺口": "多巴胺預期",  # 好奇心 → 多巴胺預期
        "恐懼訴求": "杏仁核劫持",
        "情緒共鳴": "鏡像神經元",
        "認同感": "社交認同",
        "從眾心理": "社交認同",
        "错误認知": "認知失調",
        "欲望預期": "多巴胺預期",
    }
    valid_ad_potential = ["A級直接可投", "B級小改可投", "C級需大改", "不適合投廣告"]

    hook_type = analysis.get("鉤子大類", "")
    hook_type = hook_type if hook_type in valid_hook_types else None

    visual_hammer_type = analysis.get("視覺錘類型", "")
    visual_hammer_type = visual_hammer_type if visual_hammer_type in valid_visual_hammer else None

    cta_type = analysis.get("CTA類型", "")
    cta_type = cta_type if cta_type in valid_cta_types else None

    neuro_type = analysis.get("神經科學機制", "")
    neuro_type = NEURO_ALIAS.get(neuro_type, neuro_type)  # 別名映射
    neuro_type = neuro_type if neuro_type in valid_neuro else None

    ad_potential = analysis.get("廣告投放潛力", "")
    ad_potential = ad_potential if ad_potential in valid_ad_potential else None

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
    ad_potential_detail = analysis.get('廣告投放潛力評估', {})  # 詳細分析 dict，用於頁面內容
    industry = analysis.get('產業適用性分析', {})
    invalid = analysis.get('無效因素識別', [])

    # 企劃師應用建議
    planner_tips = analysis.get('企劃師應用建議', {})
    planner_topic = planner_tips.get('本週可用選題', '待補充') if isinstance(planner_tips, dict) else '待補充'
    planner_formula = planner_tips.get('可套用的開場白公式', '待補充') if isinstance(planner_tips, dict) else '待補充'
    planner_client = planner_tips.get('適合哪類客戶', '待補充') if isinstance(planner_tips, dict) else '待補充'
    # 剪輯師應用建議
    editor_tips = analysis.get('剪輯師應用建議', {})
    editor_cut = editor_tips.get('前3秒剪輯指令', '待補充') if isinstance(editor_tips, dict) else '待補充'
    editor_timeline = editor_tips.get('節奏時間軸', '待補充') if isinstance(editor_tips, dict) else '待補充'
    editor_visual = editor_tips.get('視覺錘強調方式', '待補充') if isinstance(editor_tips, dict) else '待補充'
    editor_audio = editor_tips.get('音效與音樂建議', '待補充') if isinstance(editor_tips, dict) else '待補充'
    # 爆款三層分析
    why_viral = analysis.get('為什麼會爆款', '待補充')
    why_good = analysis.get('為什麼是好影片', '待補充')
    effect = analysis.get('能達到什麼效果', '待補充')
    video_type_val = analysis.get('影片類型', '未分類')

    content = (
        f"## 📋 企劃師速查區\n\n"
        f"> 影片類型：{video_type_val}\n\n"
        f"**🔥 為什麼會爆款？**\n{why_viral}\n\n"
        f"**🎯 能達到什麼效果？**\n{effect}\n\n"
        f"**✅ 本週可用選題：**\n{planner_topic}\n\n"
        f"**📝 可套用開場白公式：**\n{planner_formula}\n\n"
        f"**🎯 適合哪類客戶：**\n{planner_client}\n\n"
        f"---\n\n"
        f"## 🎬 剪輯師速查區\n\n"
        f"**🌟 為什麼是好影片？**\n{why_good}\n\n"
        f"**⏱️ 前3秒剪輯指令：**\n{editor_cut}\n\n"
        f"**📊 節奏時間軸：**\n{editor_timeline}\n\n"
        f"**🔨 視覺錘強調方式：**\n{editor_visual}\n\n"
        f"**🎵 音效與音樂建議：**\n{editor_audio}\n\n"
        f"---\n\n"
        f"## 🎣 鉤子類型與設計\n\n{fmt(hook)}\n\n"
        f"## 🔨 視覺錘 × 語言釘\n\n{fmt(visual_hammer)}\n\n"
        f"## 🎭 人設定位分析\n\n{fmt(persona)}\n\n"
        f"## 📐 影片結構拆解\n\n{fmt(structure)}\n\n"
        f"## 🎬 視覺設計亮點\n\n{fmt(visual_design)}\n\n"
        f"## 📣 CTA設計分析\n\n{fmt(cta)}\n\n"
        f"## 💥 爆款原因深度分析\n\n{fmt(boom_reason)}\n\n"
        f"## 📊 廣告投放潛力評估\n\n{fmt(ad_potential_detail)}\n\n"
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
                    # 根據 Notion 02 庫實際欄位名稱對滝
                    "影片標題或主題": title,
                    "原始連結": url,
                    "平台": platform,
                    "爆款數據": analysis.get("爆款數據", "待補充"),
                    "熱門音樂": analysis.get("熱門音樂", "需人工補充"),
                    "是否已借鏡": "__NO__",  # checkbox 用 __NO__ 字串（Notion MCP 格式）
                    "類別標籤": tags_json,
                    "來源類型": "Meta廣告" if "facebook.com/ads" in url or "meta" in url.lower() or "MOCK_" in url else "有機熱門",
                    "入庫日期": datetime.now().astimezone().strftime("%Y-%m-%d"),
                    "platform_video_id": analysis.get("platform_video_id") or canonical_video_identity(url)["identity"],
                    "run_id": analysis.get("run_id", ""),
                    "處理狀態": OUTCOME_UNIQUE_SUCCESS,
                    "證據狀態": analysis.get("evidence_status", EVIDENCE_INSUFFICIENT),
                    **({"觀看數": analysis.get("view_count")} if analysis.get("view_count") is not None else {}),
                    **({"作者帳號": analysis.get("uploader")} if analysis.get("uploader") else {}),
                    **({"評分": analysis.get("score")} if analysis.get("score") is not None else {}),
                    # 將內容塩入實際存在的文字欄位
                    "開頭鉤子拆解": planner_formula[:200] if planner_formula else "",
                    "結尾呼籲拆解": str(cta)[:200] if cta else "",
                    "結構拆解": str(structure)[:200] if structure else "",
                    "視覺亮點": str(visual_design)[:200] if visual_design else "",
                    "為什麼會爆": str(analysis.get("為什麼會爆款", ""))[:200],
                    **({"鉤子大類": hook_type} if hook_type else {}),
                    **({"視覺錘類型": visual_hammer_type} if visual_hammer_type else {}),
                    **({"CTA類型": cta_type} if cta_type else {}),
                    **({"神經科學機制": neuro_type} if neuro_type else {}),
                    **({"廣告投放潛力": ad_potential} if ad_potential else {})
                }
            }
        ]
    }

    # 寫入 Notion（用 --input-file 避免 shell 轉義問題）
    import tempfile as _tf
    with _tf.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as _f:
        json.dump(payload, _f, ensure_ascii=False)
        _input_file = _f.name

    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-create-pages",
        "--server", "notion",
        "--input-file", _input_file
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    try:
        os.unlink(_input_file)
    except Exception:
        pass

    if result.returncode != 0:
        err_msg = result.stderr[:300]
        # Notion MCP 未啟用時，降級存本地 JSON
        if "not found" in err_msg or "server" in err_msg.lower():
            queued_url = _save_to_local_queue(payload, url)
            raise RuntimeError(f"NOTION_QUEUED:{queued_url}")
        raise RuntimeError(f"Notion 寫入失敗：{err_msg}")

    # ── 解析回傳的頁面 URL ──────────────────────────────────────
    # stdout 格式：
    #   Tool execution result saved to: /path/to/file\n
    #   Tool execution result:\n
    #   {JSON}
    # 用 rfind 取「最後一個」marker 之後的 JSON，避免多段輸出干擾
    try:
        raw = result.stdout
        marker = "Tool execution result:\n"
        idx = raw.rfind(marker)
        if idx != -1:
            json_str = raw[idx + len(marker):].strip()
        else:
            # fallback：找第一個 '{' 開始的行
            json_str = ""
            for line in raw.split("\n"):
                line = line.strip()
                if line.startswith("{"):
                    json_str = line
                    break

        if not json_str:
            raise ValueError("找不到 JSON 輸出")

        data = json.loads(json_str)
        # Notion API 回傳格式：{"pages": [{"url": "...", "id": "..."}]}
        pages = data.get("pages", [])
        if pages and isinstance(pages, list):
            page_url = pages[0].get("url", "")
            if page_url and page_url.startswith("http"):
                if not verify_notion_page_via_mcp(page_url, url):
                    raise RuntimeError(f"NOTION_READBACK_FAILED:{page_url}")
                print(f"  ✅ 02｜爆款拆解庫 入庫成功：{page_url}")
                return page_url

        # 有時 API 回傳 {"id": "xxx", "url": "..."}（單頁格式）
        single_url = data.get("url", "")
        if single_url and single_url.startswith("http"):
            if not verify_notion_page_via_mcp(single_url, url):
                raise RuntimeError(f"NOTION_READBACK_FAILED:{single_url}")
            print(f"  ✅ 02｜爆款拆解庫 入庫成功：{single_url}")
            return single_url

        # 嘗試從 id 建構 URL
        page_id = (pages[0].get("id", "") if pages else "") or data.get("id", "")
        if page_id:
            clean_id = page_id.replace("-", "")
            constructed_url = f"https://notion.so/{clean_id}"
            if not verify_notion_page_via_mcp(constructed_url, url):
                raise RuntimeError(f"NOTION_READBACK_FAILED:{constructed_url}")
            print(f"  ✅ 02｜爆款拆解庫 入庫成功（URL 由 ID 建構）：{constructed_url}")
            return constructed_url

        raise RuntimeError(f"NOTION_WRITE_UNVERIFIED:missing_page_url:{raw[:200]}")

    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(
            f"NOTION_WRITE_UNVERIFIED:{type(e).__name__}:{e}:stdout={result.stdout[:200]}"
        ) from e


def _save_to_local_queue(payload: dict, url: str) -> str:
    """
    Notion MCP 未啟用時，將拆解結果存到本地 JSON 佇列
    路徑：<專案目錄>/data/notion_queue.json
    """
    queue_file = BASE_DIR / "data" / "notion_queue.json"
    queue_file.parent.mkdir(parents=True, exist_ok=True)

    lock_file = queue_file.with_suffix(queue_file.suffix + ".lock")
    with lock_file.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        queue = []
        if queue_file.exists():
            try:
                with open(queue_file, "r", encoding="utf-8") as f:
                    queue = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"NOTION_QUEUE_UNREADABLE:{queue_file}") from exc
            if not isinstance(queue, list):
                raise RuntimeError(f"NOTION_QUEUE_INVALID_SHAPE:{queue_file}")

        entry = {
            "url": url,
            "queued_at": datetime.now().astimezone().isoformat(),
            "payload": payload,
        }
        queue.append(entry)

        fd, tmp_name = tempfile.mkstemp(prefix="notion_queue_", suffix=".json", dir=queue_file.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(queue, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, queue_file)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    print(f"  ⚠️  Notion MCP 未啟用，已存入本地佇列（{len(queue)} 筆）")
    return f"local://notion_queue/{len(queue)}"


def _parse_slack_send_output(stdout: str) -> dict:
    raw = stdout.strip()
    if "Tool execution result:" in raw:
        raw = raw.rsplit("Tool execution result:", 1)[-1].strip()
    try:
        data = json.loads(raw)
    except Exception:
        data = {"raw": raw}

    def find_value(node, keys):
        if isinstance(node, dict):
            for key in keys:
                value = node.get(key)
                if value not in (None, ""):
                    return value
            for value in node.values():
                found = find_value(value, keys)
                if found not in (None, ""):
                    return found
        elif isinstance(node, list):
            for value in node:
                found = find_value(value, keys)
                if found not in (None, ""):
                    return found
        return None

    message_ts = str(find_value(data, ("message_ts", "message_id", "ts", "timestamp")) or "")
    channel_id = str(find_value(data, ("channel_id", "channel")) or "")
    permalink = str(find_value(data, ("permalink", "url")) or "")
    api_error = find_value(data, ("error", "error_message"))

    # Some MCP wrappers return identifiers inside a formatted result string.
    # Extract only Slack's exact timestamp/channel shapes; never infer success.
    if not message_ts:
        match = re.search(
            r"(?:message[_ ]?(?:ts|id)|\bts|timestamp)\s*[:=]\s*[\"']?(\d{9,12}\.\d{3,9})",
            raw,
            re.IGNORECASE,
        )
        if match:
            message_ts = match.group(1)
    if not channel_id:
        match = re.search(r"\b([CDG][A-Z0-9]{8,})\b", raw)
        if match:
            channel_id = match.group(1)
    if not permalink:
        match = re.search(r"https://[^\s\"']+/archives/[A-Z0-9]+/p\d+", raw)
        if match:
            permalink = match.group(0)

    return {
        "message_ts": message_ts,
        "channel_id": channel_id,
        "permalink": permalink,
        "api_error": api_error,
    }


def send_slack_dm(message: str, channel: str = None) -> dict:
    """
    透過 Slack MCP 發送訊息
    回傳結構化發送結果；沒有訊息 ID 時標為 sent_untracked。
    內建一次重試機制，避免短暂網路波動就放棄
    """
    target = channel or SLACK_AWEI_ID
    cmd = [
        "manus-mcp-cli", "tool", "call", "slack_send_message",
        "--server", "slack",
        "--input", json.dumps({
            "channel_id": target,
            "message": message
        })
    ]
    for attempt in range(2):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                parsed = _parse_slack_send_output(result.stdout)
                if parsed.get("api_error"):
                    if attempt == 0:
                        time.sleep(2)
                        continue
                    return {"success": False, "status": "api_error", "error": str(parsed["api_error"])}
                parsed.update({
                    "success": True,
                    "status": "trackable_sent" if parsed.get("message_ts") else "sent_untracked",
                    "channel_id": parsed.get("channel_id") or target,
                    "raw_output": result.stdout[:500],
                })
                return parsed
            if attempt == 0:
                print(f"  ⚠️  Slack 發送失敗，重試中... ({result.stderr[:80]})")
                time.sleep(2)
        except Exception as e:
            if attempt == 0:
                print(f"  ⚠️  Slack 發送異常，重試中... ({e})")
                time.sleep(2)
    print(f"  ❌ Slack 發送失敗（已重試）：目標頻道 {target}")
    return {"success": False, "status": "technical_error", "error": f"Slack 發送失敗：{target}"}


# ─── 主流程 ───────────────────────────────────────────────

def process_single_video(url: str, whisper_available: bool = True, run_id: str = "") -> dict:
    """Process one URL under the fail-closed data-quality contract."""
    run_id = run_id or f"run-{datetime.now().astimezone().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    identity = canonical_video_identity(url)
    canonical_url = identity["canonical_url"]
    video_id = identity["identity"]
    platform = identity["platform"] or detect_platform(canonical_url)
    base_result = {
        "success": False,
        "run_id": run_id,
        "url": url,
        "canonical_url": canonical_url,
        "video_id": identity["video_id"] or None,
        "identity": video_id,
        "platform": platform,
        "score": None,
        "score_status": "not_scored",
        "library_urls": {},
    }

    print(f"\n{'=' * 55}")
    print(f"開始拆解：{canonical_url[:80]}")
    print(f"run_id：{run_id} | identity：{video_id}")
    print(f"{'=' * 55}")

    claimed = False
    try:
        claim_status = claim_video(video_id, PROCESSED_REGISTRY, run_id)
        if claim_status == "processed":
            return {
                **base_result,
                "outcome": OUTCOME_DUPLICATE,
                "error_code": "LOCAL_REGISTRY_DUPLICATE",
                "error": "canonical identity 已在本機成功登錄",
            }
        if claim_status == "in_flight":
            return {
                **base_result,
                "outcome": OUTCOME_TECHNICAL_ERROR,
                "error_code": "IDENTITY_IN_FLIGHT",
                "error": "同一 canonical identity 正由另一個 run 處理；本次未執行",
            }
        claimed = True

        print("  [0/6] 去重檢查（fail-closed）...")
        if check_duplicate_via_mcp(canonical_url, video_id):
            return {
                **base_result,
                "outcome": OUTCOME_DUPLICATE,
                "error_code": "DUPLICATE",
                "error": "canonical URL 已存在",
            }

        print("  [1/6] 抓取 metadata...")
        metadata = fetch_video_metadata(canonical_url)

        transcript = ""
        with tempfile.TemporaryDirectory() as tmpdir:
            print("  [2/6] 下載音頻...")
            audio_path = None
            try:
                audio_path = download_video(canonical_url, tmpdir)
            except RuntimeError as exc:
                if "IP_BLOCKED" in str(exc) or "403" in str(exc) or "blocked" in str(exc).lower():
                    print("  ⚠️ IP 被封鎖；不得用空 metadata 生成完整分析")
                else:
                    raise

            if audio_path and whisper_available:
                print("  [3/6] 語音轉文字...")
                transcript = transcribe_audio(audio_path)
            elif audio_path and not whisper_available:
                print("  ⚠️ 轉錄未啟用；本項進入隔離")
            else:
                print("  ⚠️ 無可用音頻；本項進入隔離")

        # Current v3 pipeline has no frame extraction or vision model, therefore
        # visual_ready must remain False.  It cannot be enabled by environment.
        evidence = evaluate_evidence(metadata, transcript, has_visual_evidence=False)
        base_result["evidence"] = evidence
        base_result["evidence_status"] = evidence["status"]

        if VIRAL_SAFE_MODE or not may_publish_guidance(evidence):
            reasons = []
            if VIRAL_SAFE_MODE:
                reasons.append("P0_SAFE_MODE")
            if not evidence["metadata_ready"]:
                reasons.append("METADATA_INCOMPLETE")
            if not evidence["transcript_ready"]:
                reasons.append("TRANSCRIPT_UNAVAILABLE")
            if not evidence["visual_ready"]:
                reasons.append("VISUAL_EVIDENCE_UNAVAILABLE")
            return {
                **base_result,
                "outcome": OUTCOME_QUARANTINED,
                "error_code": "+".join(reasons) or "EVIDENCE_GATE",
                "error": "證據未通過發布閘門；未呼叫 AI、未寫入 Notion、未發角色通知",
            }

        print("  [4/6] GPT 拆解分析...")
        analysis = analyze_with_gpt4o(transcript, platform, canonical_url)
        analysis = sanitize_unverifiable_analysis(analysis, evidence)
        analysis.update({
            "平台": platform,
            "platform": platform,
            "逐字稿": transcript,
            "transcript": transcript,
            "source_type": "有機熱門",
            "run_id": run_id,
            "platform_video_id": video_id,
            "canonical_url": canonical_url,
            "view_count": evidence.get("view_count"),
            "uploader": evidence.get("uploader"),
            "upload_date": evidence.get("upload_date"),
        })

        score_result = score_video({
            "title": analysis.get("影片標題或主題", ""),
            "platform": platform,
            "viral_data": analysis.get("爆款數據", ""),
            "transcript": transcript,
            "hook": str(analysis.get("鉤子類型與設計") or ""),
            "visual_hammer": str(analysis.get("視覺錘分析") or ""),
            "script_structure": str(analysis.get("影片結構拆解") or ""),
            "source_type": analysis.get("source_type", "有機熱門"),
            "evidence_status": evidence["status"],
        })
        analysis["_score_result"] = score_result
        analysis["score"] = score_result.get("score")
        analysis["score_label"] = score_result.get("score_label", "未評分")
        analysis["score_status"] = score_result.get("score_status", "unknown")

        print("  [5/6] 寫入 Notion 並 read-back...")
        notion_url = write_to_notion_via_mcp(canonical_url, platform, transcript, analysis)
        registry_warning = None
        try:
            mark_locally_processed(video_id, canonical_url, notion_url, PROCESSED_REGISTRY, run_id)
        except Exception as exc:
            # Notion 已 read-back；本地 registry 是防重輔助，不能倒寫主入庫事實。
            registry_warning = f"{type(exc).__name__}: {exc}"
            print(f"  ⚠️ 本機 processed registry 寫入失敗：{registry_warning}")

        print("  [6/6] Slack 分角色通知...")
        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        title = analysis.get("影片標題或主題", "未命名")
        video_type = analysis.get("影片類型", "")
        planner_tips = analysis.get("企劃師應用建議") or {}
        editor_tips = analysis.get("剪輯師應用建議") or {}

        msg_planner = (
            f"💡 *今日爆款入庫（企劃版）* | {today}\n"
            f"<@{SLACK_XINXIN_ID}> 新素材入庫，請查收\n"
            f"影片類型：{video_type} | 平台：{platform}\n"
            f"*標題：* {title}\n\n"
            f"🔥 *為什麼會爆款？*\n{analysis.get('為什麼會爆款', '')}\n\n"
            f"✅ *本週可用選題：*\n{planner_tips.get('本週可用選題', '')}\n\n"
            f"📝 *可套用開場白公式：*\n{planner_tips.get('可套用的開場白公式', '')}\n\n"
            f"📚 *已驗證入庫：02｜爆款拆解庫*\n{notion_url}\n\n"
            "✅ *確認收到請在本訊息 thread 回覆：* `OK 小鑫`"
        )
        msg_editor = (
            f"🎬 *今日爆款入庫（剪輯版）* | {today}\n"
            f"<@{SLACK_AWEI_ID}> 新素材入庫，請查收\n"
            f"影片類型：{video_type} | 平台：{platform}\n"
            f"*標題：* {title}\n\n"
            f"⏱️ *前3秒剪輯指令：*\n{editor_tips.get('前3秒剪輯指令', '')}\n\n"
            f"📊 *節奏時間軸：*\n{editor_tips.get('節奏時間軸', '')}\n\n"
            f"📚 *已驗證入庫：02｜爆款拆解庫*\n{notion_url}\n\n"
            "✅ *確認收到請在本訊息 thread 回覆：* `OK 阿韋`"
        )

        planner_send = send_slack_dm(msg_planner, channel=SLACK_AUTO_CH)
        editor_send = send_slack_dm(msg_editor, channel=SLACK_AUTO_CH)
        notification_tracking_errors = []
        for role, recipient, send_result in (
            ("planner", SLACK_XINXIN_ID, planner_send),
            ("editor", SLACK_AWEI_ID, editor_send),
        ):
            try:
                log_delivery(
                    video_id,
                    role,
                    status=send_result.get("status", "technical_error"),
                    run_id=run_id,
                    channel_id=send_result.get("channel_id", ""),
                    message_ts=send_result.get("message_ts", ""),
                    recipient_id=recipient,
                )
            except Exception as exc:
                notification_tracking_errors.append({
                    "role": role,
                    "error": f"{type(exc).__name__}: {exc}",
                })

        return {
            **base_result,
            "success": True,
            "outcome": OUTCOME_UNIQUE_SUCCESS,
            "notion_url": notion_url,
            "title": title,
            "video_type": video_type,
            "analysis": analysis,
            "score": score_result.get("score"),
            "score_label": score_result.get("score_label", "未評分"),
            "score_status": score_result.get("score_status", "unknown"),
            "evidence": evidence,
            "evidence_status": evidence["status"],
            "notification_status": {
                "planner": planner_send.get("status"),
                "editor": editor_send.get("status"),
            },
            "notification_tracking_errors": notification_tracking_errors,
            "registry_warning": registry_warning,
        }

    except Exception as exc:
        error_text = str(exc)
        if error_text.startswith("NOTION_QUEUED:"):
            outcome, code = OUTCOME_QUEUED, "NOTION_QUEUED"
        elif error_text.startswith(("NOTION_READBACK_FAILED:", "NOTION_WRITE_UNVERIFIED:")):
            outcome, code = OUTCOME_WRITE_UNVERIFIED, error_text.split(":", 1)[0]
        else:
            outcome, code = OUTCOME_TECHNICAL_ERROR, error_text.split(":", 1)[0] or type(exc).__name__
        print(f"\n  ❌ {outcome}：{error_text}")
        return {
            **base_result,
            "outcome": outcome,
            "error_code": code,
            "error": error_text,
        }
    finally:
        if claimed:
            try:
                release_video_claim(video_id, PROCESSED_REGISTRY, run_id)
            except Exception as exc:
                print(f"  ⚠️ identity claim 釋放失敗：{type(exc).__name__}: {exc}")


def _outcome_counts(results: list) -> dict:
    counts = {
        OUTCOME_UNIQUE_SUCCESS: 0,
        OUTCOME_DUPLICATE: 0,
        OUTCOME_QUARANTINED: 0,
        OUTCOME_QUEUED: 0,
        OUTCOME_WRITE_UNVERIFIED: 0,
        OUTCOME_TECHNICAL_ERROR: 0,
    }
    for result in results:
        outcome = result.get("outcome", OUTCOME_TECHNICAL_ERROR)
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def send_batch_reports(results: list, run_id: str) -> dict:
    """Send the daily and health reports only after scoring/distribution finishes."""
    counts = _outcome_counts(results)
    successes = [row for row in results if row.get("outcome") == OUTCOME_UNIQUE_SUCCESS]
    downstream_failures = [
        row for row in successes
        if row.get("distribution_status") in {"partial_failure", "technical_error"}
    ]
    untrackable_notifications = 0
    for row in successes:
        for status in (row.get("notification_status") or {}).values():
            if status != "trackable_sent":
                untrackable_notifications += 1
    today = datetime.now().astimezone().strftime("%Y-%m-%d")

    lines = [
        f"📊 *今日爆款資料管線日報* | {today}",
        f"run_id：`{run_id}`",
        (
            f"選入：*{len(results)}* | 唯一有效新增：*{counts[OUTCOME_UNIQUE_SUCCESS]}* | "
            f"重複：{counts[OUTCOME_DUPLICATE]} | 證據隔離：{counts[OUTCOME_QUARANTINED]} | "
            f"技術錯誤：{counts[OUTCOME_TECHNICAL_ERROR]}"
        ),
        (
            f"本地待重送：{counts[OUTCOME_QUEUED]} | "
            f"寫入未驗證：{counts[OUTCOME_WRITE_UNVERIFIED]}"
        ),
        (
            f"下游分配部分／技術失敗：{len(downstream_failures)} | "
            f"無法綁定 Slack message_ts：{untrackable_notifications}"
        ),
        "",
    ]
    if not successes:
        lines.append("⚠️ 今日沒有可發布的唯一有效素材；不產生選題、剪輯或拍攝建議。")
    for index, row in enumerate(successes, 1):
        score_label = row.get("score_label") or "未評分"
        lines.append(
            f"{index}. [{row.get('platform', '')}] {row.get('title', '')} | "
            f"{score_label} | evidence={row.get('evidence_status', 'unknown')} | "
            f"distribution={row.get('distribution_status', 'not_run')}"
        )
        lines.append(f"   📚 {row.get('notion_url', '')}")
        if row.get("library_urls"):
            libraries = " | ".join(key for key, value in row["library_urls"].items() if value)
            lines.append(f"   📦 {libraries}")

    daily_send = send_slack_dm("\n".join(lines), channel=SLACK_AUTO_CH)

    health_lines = [
        f"🛡️ *系統健康摘要* | {today}",
        f"run_id：`{run_id}`",
        f"selected={len(results)} | " + " | ".join(f"{key}={value}" for key, value in counts.items()),
    ]
    problem_rows = [
        row for row in results
        if row.get("outcome") != OUTCOME_UNIQUE_SUCCESS
        or row.get("distribution_status") in {"partial_failure", "technical_error"}
        or row.get("registry_warning")
        or row.get("notification_tracking_errors")
        or any(
            status != "trackable_sent"
            for status in (row.get("notification_status") or {}).values()
        )
    ]
    for row in problem_rows:
        if row.get("error_code"):
            detail = row["error_code"]
        elif row.get("distribution_status") in {"partial_failure", "technical_error"}:
            detail = row["distribution_status"]
        elif row.get("registry_warning"):
            detail = "LOCAL_REGISTRY_WARNING"
        else:
            detail = "SLACK_UNTRACKABLE_OR_TRACKER_ERROR"
        health_lines.append(
            f"• {row.get('outcome')} | {detail} | "
            f"{row.get('canonical_url', row.get('url', ''))}"
        )
    dennis_id = os.environ.get("SLACK_DENNIS_ID", "U0ARRQS3XPS")
    health_send = send_slack_dm("\n".join(health_lines), channel=dennis_id)
    return {"daily": daily_send, "health": health_send, "counts": counts}


def process_batch(urls: list, whisper_available: bool = True, run_id: str = "") -> list:
    """Process a batch without publishing reports prematurely."""
    run_id = run_id or f"run-{datetime.now().astimezone().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    results = []
    print("\n🏭 爆款短影音拆解工廠啟動")
    print(f"run_id：{run_id} | 本次選入：{len(urls)} 支")

    for index, url in enumerate(urls, 1):
        print(f"\n[{index}/{len(urls)}]", end="")
        results.append(process_single_video(url, whisper_available, run_id=run_id))

    counts = _outcome_counts(results)
    print(f"\n{'=' * 55}")
    print("結果：" + " | ".join(f"{key}={value}" for key, value in counts.items()))
    return results


# ─── 執行入口 ─────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python3 viral_factory.py <影片連結> [連結2] ...")
        sys.exit(1)

    urls = sys.argv[1:]
    whisper_ok = True  # 若 Whisper 額度不足，改為 False
    cli_run_id = f"run-{datetime.now().astimezone().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    cli_results = process_batch(urls, whisper_ok, run_id=cli_run_id)
    send_batch_reports(cli_results, cli_run_id)
