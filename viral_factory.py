"""爆款短影音拆解工廠 v3.0
好創整合行銷 | 子權 2026-06-10

流程：影片連結 → yt-dlp 下載 → Whisper 轉文字 → GPT-4o 拆解 → Notion MCP 寫入 → Slack MCP 通知
架構：Whisper 用子權的 OpenAI Key（直接呼叫 api.openai.com）
      GPT-4o 用沙盒免費代理（api.manus.im）
      Notion / Slack 用 MCP 工具（不需要額外 Key）
"""

import os
import json
import subprocess
import tempfile
import time
from datetime import datetime
from execution_tracker import log_delivery
from pathlib import Path

from openai import OpenAI

# ─── 設定區 ───────────────────────────────────────────────
# Whisper 專用（直接呼叫 OpenAI 原始 API）
WHISPER_API_KEY = os.environ.get(
    "WHISPER_API_KEY",
    os.environ.get("OPENAI_API_KEY", "")
)

# Notion 資料庫 ID（02 爆款拆解庫）
NOTION_DB_ID = "82097a06-fae5-83bd-a8c3-87236d3713aa"

# Slack 設定
SLACK_AWEI_ID    = "U0B4FG0ER89"   # 阿韋 User ID（用於 @mention）
SLACK_XINXIN_ID  = "U0BA2DKQ7GF"   # 小鑫 User ID（用於 @mention）
SLACK_TEAM_CH    = "C0AQG307XJT"   # #all-團隊主頻道
SLACK_AUTO_CH    = "C0AUH4QKF5M"   # #自動化訊息來源（影音類別）

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
                "view_count": info.get("view_count", 0),
                "like_count": info.get("like_count", 0),
                "uploader": info.get("uploader", ""),
                "duration": info.get("duration", 0),
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
        temperature=0.3
    )
    # 沙盒代理在模型不支援時會回傳 error 欄位而非 choices
    if response.choices is None:
        err = getattr(response, 'error', 'Unknown error')
        raise RuntimeError(f"GPT API 錯誤：{err}")
    return json.loads(response.choices[0].message.content)


def check_duplicate_via_mcp(url: str) -> bool:
    """透過 Notion MCP 查詢是否已有此連結"""
    import re as _re
    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-query-data-sources",
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
        raw = result.stdout.strip()
        raw = _re.sub(r'\x1b\[[0-9;]*m', '', raw)
        if "Tool execution result:" in raw:
            raw = raw.split("Tool execution result:")[-1].strip()
        data = json.loads(raw)
        return len(data.get("results", data.get("pages", []))) > 0
    except Exception:
        return False


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
    tags_json = json.dumps(tags, ensure_ascii=False)

    # 結構化標籤欄位（對應 Notion 選項）
    valid_hook_types = ["疑問式", "否定式", "衝突式", "數字式", "警告式", "揭秘式", "前後對比式", "大膽宣言式"]
    valid_visual_hammer = ["道具型", "構圖型", "色彩型", "字幕型", "人物型", "場景型", "動作型"]
    valid_cta_types = ["留言誘餌", "私訊獲取", "追蹤鉤子", "到店導流", "加LINE", "預約", "點連結購買", "填表單"]
    valid_neuro = ["杏仁核劫持", "多巴胺預期", "鏡像神經元", "認知失調", "損失厭惡", "社交認同"]
    valid_ad_potential = ["A級直接可投", "B級小改可投", "C級需大改", "不適合投廣告"]

    hook_type = analysis.get("鉤子大類", "")
    hook_type = hook_type if hook_type in valid_hook_types else None

    visual_hammer_type = analysis.get("視覺錘類型", "")
    visual_hammer_type = visual_hammer_type if visual_hammer_type in valid_visual_hammer else None

    cta_type = analysis.get("CTA類型", "")
    cta_type = cta_type if cta_type in valid_cta_types else None

    neuro_type = analysis.get("神經科學機制", "")
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
    ad_potential = analysis.get('廣告投放潛力評估', {})
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
                    "來源類型": "Meta廣告（🌍英國）" if "facebook.com/ads" in url or "meta" in url.lower() or "MOCK_" in url else "有機熱門",
                    "影片類型": video_type_val,
                    "本週可用選題": planner_topic[:200] if planner_topic else "",
                    "可套用開場白公式": planner_formula[:200] if planner_formula else "",
                    "適合哪類客戶": planner_client[:200] if planner_client else "",
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
            return _save_to_local_queue(payload, url)
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
                print(f"  ✅ 02｜爆款拆解庫 入庫成功：{page_url}")
                return page_url

        # 有時 API 回傳 {"id": "xxx", "url": "..."}（單頁格式）
        single_url = data.get("url", "")
        if single_url and single_url.startswith("http"):
            print(f"  ✅ 02｜爆款拆解庫 入庫成功：{single_url}")
            return single_url

        # 嘗試從 id 建構 URL
        page_id = (pages[0].get("id", "") if pages else "") or data.get("id", "")
        if page_id:
            clean_id = page_id.replace("-", "")
            constructed_url = f"https://notion.so/{clean_id}"
            print(f"  ✅ 02｜爆款拆解庫 入庫成功（URL 由 ID 建構）：{constructed_url}")
            return constructed_url

        print(f"  ⚠️  Notion 頁面已建立但無法取得 URL，原始輸出：{raw[:200]}")
        return "https://notion.so/82097a06fae583bda8c387236d3713aa"

    except Exception as e:
        print(f"  ⚠️  Notion URL 解析失敗（頁面可能已建立）：{e}")
        print(f"      stdout 前 300 字：{result.stdout[:300]}")
        return "https://notion.so/82097a06fae583bda8c387236d3713aa"


def _save_to_local_queue(payload: dict, url: str) -> str:
    """
    Notion MCP 未啟用時，將拆解結果存到本地 JSON 佇列
    路徑：/home/ubuntu/viral_factory/data/notion_queue.json
    """
    queue_file = Path("/home/ubuntu/viral_factory/data/notion_queue.json")
    queue_file.parent.mkdir(parents=True, exist_ok=True)

    queue = []
    if queue_file.exists():
        try:
            with open(queue_file, "r", encoding="utf-8") as f:
                queue = json.load(f)
        except Exception:
            queue = []

    entry = {
        "url": url,
        "queued_at": datetime.now().isoformat(),
        "payload": payload
    }
    queue.append(entry)

    with open(queue_file, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)

    print(f"  ⚠️  Notion MCP 未啟用，已存入本地佇列（{len(queue)} 筆）")
    return f"local://notion_queue/{len(queue)}"


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
            # Step 1: 下載（失敗時自動降級為無音頻模式）
            print(f"  [1/5] 下載影片...")
            audio_path = None
            ip_blocked = False
            try:
                audio_path = download_video(url, tmpdir)
                size_kb = Path(audio_path).stat().st_size / 1024
                print(f"  下載完成：{Path(audio_path).name}（{size_kb:.0f} KB）")
            except RuntimeError as e:
                if "IP_BLOCKED" in str(e) or "403" in str(e) or "blocked" in str(e).lower():
                    ip_blocked = True
                    print(f"  ⚠️  IP 被封鎖，自動降級為無音頻模式")
                else:
                    raise  # 其他錯誤正常拋出

            # Step 2: 轉文字（或用 metadata 代替）
            if ip_blocked or audio_path is None:
                # 降級：用 yt-dlp metadata 取得影片標題和描述
                print(f"  [2/5] 無音頻模式：抓取影片 metadata...")
                meta = fetch_video_metadata(url)
                title_text = meta.get("title", "")
                desc_text = meta.get("description", "")
                view_count = meta.get("view_count", 0)
                uploader = meta.get("uploader", "")
                # 用 metadata 組合成「逆字稿代替」
                transcript = (
                    f"⚠️ 無法下載音頻（IP 被封鎖），以文字資訊進行拆解\n\n"
                    f"影片標題：{title_text}\n"
                    f"影片描述：{desc_text}\n"
                    f"作者：{uploader}\n"
                    f"觀看數：{view_count:,}\n"
                    f"來源連結：{url}"
                )
                print(f"  metadata 抓取完成：{title_text[:40]}")
            elif not whisper_available:
                transcript = "（Whisper API 額度不足，逐字稿待補充）"
                print(f"  [2/5] 跳過轉錄（API 額度不足）")
            else:
                print(f"  [2/5] Whisper 語音轉文字...")
                transcript = transcribe_audio(audio_path)
                if not transcript.strip():
                    transcript = "（無語音內容，純視覺影片）"
                print(f"  轉錄完成：{len(transcript)} 字")

            # Step 3: AI 拆解
            print(f"  [3/5] GPT 拆解分析...")
            analysis = analyze_with_gpt4o(transcript, platform, url)
            print(f"  拆解完成：{analysis.get('影片標題或主題', '')[:30]}")

            # Step 4: 寫入 Notion
            print(f"  [4/5] 寫入 Notion...")
            notion_url = write_to_notion_via_mcp(url, platform, transcript, analysis)
            print(f"  寫入完成：{notion_url}")

            # Step 5: Slack 分角色通知
            print(f"  [5/5] Slack 分角色通知（小鑫企劃版 + 阿韋剪輯版）...")
            today = datetime.now().strftime("%Y-%m-%d")
            title = analysis.get('影片標題或主題', '未命名')
            video_type = analysis.get('影片類型', '')
            score_label = analysis.get('score_label', '')
            why_viral = analysis.get('為什麼會爆款', '')
            effect = analysis.get('能達到什麼效果', '')

            # 企劃師版通知（小鑫）
            planner_tips = analysis.get('企劃師應用建議', {})
            topic = planner_tips.get('本週可用選題', '') if isinstance(planner_tips, dict) else ''
            formula = planner_tips.get('可套用的開場白公式', '') if isinstance(planner_tips, dict) else ''
            client_fit = planner_tips.get('適合哪類客戶', '') if isinstance(planner_tips, dict) else ''

            # 判斷是否為 Meta 國外廣告
            is_meta_foreign = "facebook.com/ads" in url or "meta" in url.lower() or "MOCK_" in url
            foreign_tag = "\n> 🌍 此為國外廣告（英國市場），用於學習創意手法，不代表台灣市場現況" if is_meta_foreign else ""

            msg_planner = (
                f"💡 *今日爆款入庫（企劃版）* | {today}\n"
                f"<@{SLACK_XINXIN_ID}> 新素材入庫，請查收\n"
                f"影片類型：{video_type} | 平台：{platform}{foreign_tag}\n"
                f"*標題：* {title}\n\n"
                f"🔥 *為什麼會爆款？*\n{why_viral}\n\n"
                f"🎯 *能達到什麼效果？*\n{effect}\n\n"
                f"✅ *本週可用選題：*\n{topic}\n\n"
                f"📝 *可套用開場白公式：*\n{formula}\n\n"
                f"🎯 *適合哪類客戶：*\n{client_fit}\n\n"
                f"📚 *已入庫：02｜爆款拆解庫*\n{notion_url}"
            )

            # 剪輯師版通知（阿韋）
            editor_tips = analysis.get('剪輯師應用建議', {})
            cut_cmd = editor_tips.get('前3秒剪輯指令', '') if isinstance(editor_tips, dict) else ''
            timeline = editor_tips.get('節奏時間軸', '') if isinstance(editor_tips, dict) else ''
            visual_tip = editor_tips.get('視覺錘強調方式', '') if isinstance(editor_tips, dict) else ''
            audio_tip = editor_tips.get('音效與音樂建議', '') if isinstance(editor_tips, dict) else ''
            music_trend = editor_tips.get('熱門音樂趨勢', '') if isinstance(editor_tips, dict) else ''
            edit_tips = editor_tips.get('剪輯技巧建議', '') if isinstance(editor_tips, dict) else ''
            why_good = analysis.get('為什麼是好影片', '')

            msg_editor = (
                f"🎬 *今日爆款入庫（剪輯版）* | {today}\n"
                f"<@{SLACK_AWEI_ID}> 新素材入庫，請查收\n"
                f"影片類型：{video_type} | 平台：{platform}{foreign_tag}\n"
                f"*標題：* {title}\n\n"
                f"🌟 *為什麼是好影片？*\n{why_good}\n\n"
                f"⏱️ *前3秒剪輯指令：*\n{cut_cmd}\n\n"
                f"📊 *節奏時間軸：*\n{timeline}\n\n"
                f"🔨 *視覺錘強調方式：*\n{visual_tip}\n\n"
                f"🎵 *音效與音樂建議：*\n{audio_tip}\n\n"
                f"🎶 *熱門音樂趨勢：*\n{music_trend}\n\n"
                f"✂️ *剪輯技巧建議：*\n{edit_tips}\n\n"
                f"📚 *已入庫：02｜爆款拆解庫*\n{notion_url}"
            )

            # 發送兩則分角色通知，並記錄送達狀態
            # 加入互動按鈕建議 (Slack Block Kit 格式需透過 API，目前透過文字指令引導)
            msg_planner += "\n\n✅ *確認收到請回覆：* `OK 小鑫`"
            msg_editor += "\n\n✅ *確認收到請回覆：* `OK 阿韋`"
            
            if send_slack_dm(msg_planner, channel=SLACK_AUTO_CH):
                log_delivery(video_id, "planner", status="delivered")
            
            if send_slack_dm(msg_editor, channel=SLACK_AUTO_CH):
                log_delivery(video_id, "editor", status="delivered")
            print(f"  通知已發送（企劃版 + 剪輯版）")

            print(f"\n  ✅ 完成！")
            return {
                "success": True,
                "notion_url": notion_url,
                "title": analysis.get("影片標題或主題", ""),
                "platform": platform,
                "url": url,
                "video_type": analysis.get("影片類型", ""),
                "score": analysis.get("score", 0),
                "analysis": analysis,
                "library_urls": {}  # 由 daily_run.py 的 distribute_video 填入
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

    # 統計影片類型分佈與評分分佈
    type_count = {}
    score_dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for r in success_items:
        vtype = r.get('video_type', r.get('analysis', {}).get('影片類型', '未分類'))
        type_count[vtype] = type_count.get(vtype, 0) + 1
        score = r.get('score', 0)
        if isinstance(score, int) and score in score_dist:
            score_dist[score] += 1

    type_summary = ' | '.join([f"{k}:{v}支" for k, v in type_count.items()]) or '無分類資料'
    score_summary = ' '.join([f"{s}分:{n}支" for s, n in score_dist.items() if n > 0]) or '無評分資料'

    # 主頻道日報（小鑫 + 阿韋）
    lines = [f"📊 *今日爆款入庫日報* | {today}\n"]
    lines.append(f"<@{SLACK_XINXIN_ID}> <@{SLACK_AWEI_ID}> 今日入庫完成\n")
    lines.append(f"入庫：*{len(success_items)} 支* | 失敗/跳過：{fail_count} 支")
    lines.append(f"影片類型：{type_summary}")
    lines.append(f"評分分佈：{score_summary}\n")
    for i, r in enumerate(success_items, 1):
        vtype = r.get('video_type', '')
        score = r.get('score', '-')
        notion_url_item = r.get('notion_url', '')
        lines.append(f"{i}. [{r.get('platform','')}][{vtype}] {r.get('title','')} ★{score}")
        lines.append(f"   📚 02｜爆款拆解庫 → {notion_url_item}")
        # 附上素材庫分配狀態
        lib_urls = r.get('library_urls', {})
        if lib_urls:
            lib_summary = ' | '.join([f"{k}✓" for k, v in lib_urls.items() if v])
            lines.append(f"   📦 素材庫：{lib_summary}")
    send_slack_dm("\n".join(lines), channel=SLACK_AUTO_CH)

    # 子權健康版 DM
    SLACK_DENNIS_ID = os.environ.get('SLACK_DENNIS_ID', 'U07MHPJKQ8V')
    fail_rate = fail_count / (len(results) or 1) * 100
    health_lines = [
        f"🛡️ *系統健康摘要* | {today}",
        f"執行結果：成功 {len(success_items)} 支 | 失敗 {fail_count} 支 | 失敗率 {fail_rate:.0f}%",
        f"影片類型分佈：{type_summary}",
        f"評分分佈：{score_summary}",
    ]
    if fail_count > 0:
        fail_urls = [r.get('url', '') for r in results if not r.get('success') and r.get('error') != 'duplicate']
        if fail_urls:
            health_lines.append(f"失敗影片：{', '.join(fail_urls[:2])}")
    health_lines.append(f"庫房狀態：請至 Notion 02爆款拆解庫查看")
    send_slack_dm("\n".join(health_lines), channel=SLACK_DENNIS_ID)

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
