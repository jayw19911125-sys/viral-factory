"""
script_scorer.py
影片熱門程度評分腳本（1-5分）
評分依據：播放量、互動率、完播率指標、鉤子強度、視覺錘清晰度
"""

import os
import json
from openai import OpenAI

# 使用沙盒免費代理（與 viral_factory.py 一致），避免消耗子權的 OpenAI 額度
client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY", ""),
    base_url=os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
)

SCORE_PROMPT = """
你是台灣頂尖短影音策略師，專門評估影片的爆款潛力與熱門程度。

請根據以下資訊，對這支影片進行「熱門程度評分」（1-5分）：

影片資訊：
- 標題/主題：{title}
- 平台：{platform}
- 爆款數據：{viral_data}
- 逐字稿：{transcript}
- 鉤子設計：{hook}
- 視覺錘分析：{visual_hammer}
- 腳本結構：{script_structure}
- 來源類型：{source_type}

評分標準：
5分（最高）：完播率極高、鉤子強烈、視覺錘清晰、可直接複製套用、跨產業移植性強
4分（高）：鉤子有效、結構完整、有明確視覺錘、可小改後套用
3分（中）：有部分亮點但不夠完整，需要較多改造才能套用
2分（低）：鉤子薄弱或視覺錘不清晰，參考價值有限
1分（最低）：無明顯爆款特徵，不建議借鏡

同時判斷影片類型：
- IP型：個人品牌建立、人設展示、知識分享、生活記錄型
- 導購型：產品展示、廣告投放、帶貨轉換、服務推廣型

請以 JSON 格式回覆：
{{
  "score": 1-5的整數,
  "score_label": "5分最高" 或 "4分高" 或 "3分中" 或 "2分低" 或 "1分最低",
  "content_type": "IP型" 或 "導購型",
  "score_reason": "50字以內的評分理由",
  "key_strength": "這支影片最值得借鏡的一個點",
  "improvement": "如果要提升到5分，最需要改的一件事"
}}
"""


def score_video(video_data: dict) -> dict:
    """
    對單支影片進行評分
    video_data 需包含：title, platform, viral_data, transcript, hook, visual_hammer, script_structure, source_type
    回傳：score(int), score_label, content_type, score_reason, key_strength, improvement
    """
    prompt = SCORE_PROMPT.format(
        title=video_data.get("title", ""),
        platform=video_data.get("platform", ""),
        viral_data=video_data.get("viral_data", ""),
        transcript=video_data.get("transcript", "")[:500],  # 只取前500字
        hook=video_data.get("hook", ""),
        visual_hammer=video_data.get("visual_hammer", ""),
        script_structure=video_data.get("script_structure", ""),
        source_type=video_data.get("source_type", "有機熱門"),
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=800,  # 評分輸出簡短，800 token 足夠
        )
        raw_content = response.choices[0].message.content
        # 防護：移除 GPT 偶爾輸出的 Markdown 代碼塊標記（```json ... ```）
        if raw_content.strip().startswith("```"):
            lines = raw_content.strip().split("\n")
            raw_content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        result = json.loads(raw_content)
        return result
    except Exception as e:
        print(f"[評分失敗] {e}")
        return {
            "score": 3,
            "score_label": "3分中",
            "content_type": "IP型",
            "score_reason": "評分失敗，預設中等分數",
            "key_strength": "無法判斷",
            "improvement": "無法判斷",
        }


def is_high_score(score_result: dict) -> bool:
    """判斷是否為高分影片（4分或5分）"""
    return score_result.get("score", 0) >= 4


if __name__ == "__main__":
    # 測試用
    test_data = {
        "title": "測試影片",
        "platform": "TikTok",
        "viral_data": "播放量：50萬，按讚：2萬，留言：500",
        "transcript": "你知道為什麼你的廣告一直燒錢卻沒效嗎？因為你犯了這個錯誤...",
        "hook": "疑問式鉤子：你知道為什麼...",
        "visual_hammer": "人物型：直視鏡頭說話",
        "script_structure": "Hook(0-3秒) + 痛點放大(3-20秒) + 解法(20-45秒) + CTA(45-60秒)",
        "source_type": "有機熱門",
    }
    result = score_video(test_data)
    print(json.dumps(result, ensure_ascii=False, indent=2))
