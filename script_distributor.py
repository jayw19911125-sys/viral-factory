"""
script_distributor.py
素材分配腳本：將拆解結果按類型分配到各 Notion 庫房
負責將 viral_factory 拆解的每支影片，無遺漏、無浪費地分配到對應庫房

庫房對應：
- 02｜爆款拆解庫：所有影片（完整拆解）
- 03｜開頭鉤子庫：鉤子公式沉澱
- 04｜結尾呼籲庫：CTA公式沉澱
- 05｜腳本結構庫：結構公式沉澱
- 06｜視覺錘庫：視覺錘類型沉澱
- 07｜語言釘庫：語言釘公式沉澱
- 35｜已驗證熱門腳本庫（IP型 / 導購型）：高分影片（4-5分）的腳本沉澱
"""

import os
import json
import subprocess
import tempfile
from script_scorer import score_video, is_high_score

# Notion 庫房 ID 對照表（更改庫房時只需改 .env，不需動程式碼）
NOTION_DB_IDS = {
    "爆款拆解庫": os.environ.get("NOTION_DB_MAIN",         "82097a06-fae5-83bd-a8c3-87236d3713aa"),  # 02 庫
    "開頭鉤子庫": os.environ.get("NOTION_DB_HOOK",         "44197a06-fae5-8363-b6c4-015bde8b7d9e"),  # 03 庫
    "結尾呼籲庫": os.environ.get("NOTION_DB_CTA",          "b9c97a06-fae5-8345-89c5-815c687e348f"),  # 04 庫
    "腳本結構庫": os.environ.get("NOTION_DB_STRUCTURE",    "6c497a06-fae5-823c-98d6-017d48acb70d"),  # 05 庫
    "視覺錘庫":   os.environ.get("NOTION_DB_VISUAL",      "bcbe1980-6529-4b6b-be2c-a2fcb89bc778"),  # 06 庫
    "語言釘庫":   os.environ.get("NOTION_DB_VERBAL",      "dc9851e2-e611-4231-b01b-e85ef7afd9b5"),  # 07 庫
    "IP型腳本庫": os.environ.get("NOTION_DB_IP_SCRIPT",   "efc0711f-f496-4eec-b78f-d05de9cb9653"),  # 35 子庫
    "導購型腳本庫": os.environ.get("NOTION_DB_SALES_SCRIPT", "461772ac-895a-4f8c-b5a7-f5305ecc521b"), # 35 子庫
}

# 庫房顯示名稱（用於 Slack 通知）
LIBRARY_DISPLAY = {
    "開頭鉤子庫": "03｜開頭鉤子庫",
    "結尾呼籲庫": "04｜結尾呼籲庫",
    "腳本結構庫": "05｜腳本結構庫",
    "視覺錘庫": "06｜視覺錘庫",
    "語言釘庫": "07｜語言釘庫",
    "IP型腳本庫": "35｜IP型腳本庫",
    "導購型腳本庫": "35｜導購型腳本庫",
}


def _parse_notion_url(stdout: str, db_key: str) -> str:
    """
    解析 manus-mcp-cli 的 stdout，取得 Notion 頁面 URL
    stdout 格式：
      Tool execution result saved to: /path/to/file
      Tool execution result:
      {JSON}
    回傳真實 URL 或由新頁 ID 建構的 URL；解析失敗必須回傳空字串。
    """
    try:
        raw = stdout
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
            return ""

        data = json.loads(json_str)

        # 格式一：{"pages": [{"url": "...", "id": "..."}]}
        pages = data.get("pages", [])
        if pages and isinstance(pages, list):
            page_url = pages[0].get("url", "")
            if page_url and page_url.startswith("http"):
                return page_url
            # 從 id 建構
            page_id = pages[0].get("id", "")
            if page_id:
                return f"https://notion.so/{page_id.replace('-', '')}"

        # 格式二：{"url": "...", "id": "..."}（單頁格式）
        single_url = data.get("url", "")
        if single_url and single_url.startswith("http"):
            return single_url

        page_id = data.get("id", "")
        if page_id:
            return f"https://notion.so/{page_id.replace('-', '')}"

    except Exception:
        pass

    return ""


def call_notion_mcp(tool_name: str, input_data: dict, db_key: str = "") -> dict:
    """
    呼叫 Notion MCP 工具，回傳 {success, url, raw_output}
    使用 --input-file 避免 shell 轉義問題
    """
    # 寫入臨時 JSON 檔案
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
        json.dump(input_data, f, ensure_ascii=False)
        input_file = f.name

    cmd = [
        "manus-mcp-cli", "tool", "call", tool_name,
        "--server", "notion",
        "--input-file", input_file
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        try:
            os.unlink(input_file)
        except Exception:
            pass

        if result.returncode != 0:
            print(f"[MCP 呼叫失敗] {tool_name}: {result.stderr[:200]}")
            return {"success": False, "url": "", "error": result.stderr[:200]}

        page_url = _parse_notion_url(result.stdout, db_key)
        if not page_url:
            return {
                "success": False,
                "url": "",
                "error": "Notion 回傳成功碼但沒有可驗證的新頁 URL",
                "raw_output": result.stdout[:500],
            }
        return {"success": True, "url": page_url, "raw_output": result.stdout[:500]}

    except Exception as e:
        try:
            os.unlink(input_file)
        except Exception:
            pass
        print(f"[MCP 呼叫異常] {tool_name}: {e}")
        return {"success": False, "url": "", "error": str(e)}


def _get_nested(analysis: dict, *keys, default=""):
    """安全取得巢狀 dict 的值，支援多層 key"""
    val = analysis
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k, default)
        else:
            return default
    return val if val is not None else default


def write_to_hook_library(analysis: dict, source_url: str) -> str:
    """將鉤子公式寫入 03｜開頭鉤子庫，回傳頁面 URL"""
    hook_data = analysis.get("鉤子類型與設計", {})
    hook_type = _get_nested(hook_data, "鉤子類型") or analysis.get("鉤子大類", "")
    hook_formula = _get_nested(hook_data, "可套用公式") or _get_nested(hook_data, "鉤子公式", "")
    hook_design = _get_nested(hook_data, "鉤子設計說明") or _get_nested(hook_data, "設計說明", "")
    industry_data = analysis.get("產業適用性分析", {})
    industry = _get_nested(industry_data, "原始產業") or "通用"
    platform = analysis.get("平台", "") or analysis.get("platform", "")
    neuroscience = analysis.get("神經科學機制", "")

    if not hook_formula and not hook_type:
        return ""

    content = f"""# {hook_type}｜{str(hook_formula)[:30]}

**鉤子類型**：{hook_type}
**完整公式**：{hook_formula}
**適用產業**：{industry}
**平台**：{platform}
**來源影片**：{source_url}
**神經科學機制**：{neuroscience}

## 使用說明
{hook_design}
"""
    res = call_notion_mcp("notion-create-pages", {
        "data_source_id": NOTION_DB_IDS["開頭鉤子庫"],
        "pages": [{"content": content}]
    }, db_key="開頭鉤子庫")

    page_url = res.get("url", "")
    if res.get("success"):
        print(f"[✓] 鉤子公式已寫入 03｜開頭鉤子庫：{hook_type} → {page_url}")
    else:
        print(f"[✗] 03 庫寫入失敗：{res.get('error', '')}")
    return page_url


def write_to_cta_library(analysis: dict, source_url: str) -> str:
    """將 CTA 公式寫入 04｜結尾呼籲庫，回傳頁面 URL"""
    cta_data = analysis.get("CTA設計分析", {})
    cta_type = _get_nested(cta_data, "CTA類型") or analysis.get("CTA類型", "")
    cta_design = _get_nested(cta_data, "設計分析") or _get_nested(cta_data, "CTA設計", "")
    industry_data = analysis.get("產業適用性分析", {})
    industry = _get_nested(industry_data, "原始產業") or "通用"

    if not cta_design and not cta_type:
        return ""

    content = f"""# {cta_type}｜{industry}

**CTA類型**：{cta_type}
**設計分析**：{cta_design}
**適用產業**：{industry}
**來源影片**：{source_url}
"""
    res = call_notion_mcp("notion-create-pages", {
        "data_source_id": NOTION_DB_IDS["結尾呼籲庫"],
        "pages": [{"content": content}]
    }, db_key="結尾呼籲庫")

    page_url = res.get("url", "")
    if res.get("success"):
        print(f"[✓] CTA公式已寫入 04｜結尾呼籲庫：{cta_type} → {page_url}")
    else:
        print(f"[✗] 04 庫寫入失敗：{res.get('error', '')}")
    return page_url


def write_to_structure_library(analysis: dict, source_url: str) -> str:
    """將腳本結構寫入 05｜腳本結構庫，回傳頁面 URL"""
    structure_data = analysis.get("影片結構拆解", {})
    script_structure = _get_nested(structure_data, "結構公式") or str(structure_data) if structure_data else ""
    industry_data = analysis.get("產業適用性分析", {})
    industry = _get_nested(industry_data, "原始產業") or "通用"
    platform = analysis.get("平台", "") or analysis.get("platform", "")

    if not script_structure:
        return ""

    content = f"""# {industry}｜{platform} 腳本結構

**結構公式**：
{script_structure}

**適用產業**：{industry}
**平台**：{platform}
**來源影片**：{source_url}
"""
    res = call_notion_mcp("notion-create-pages", {
        "data_source_id": NOTION_DB_IDS["腳本結構庫"],
        "pages": [{"content": content}]
    }, db_key="腳本結構庫")

    page_url = res.get("url", "")
    if res.get("success"):
        print(f"[✓] 腳本結構已寫入 05｜腳本結構庫：{industry} → {page_url}")
    else:
        print(f"[✗] 05 庫寫入失敗：{res.get('error', '')}")
    return page_url


def write_to_visual_hammer_library(analysis: dict, source_url: str) -> str:
    """將視覺錘分析寫入 06｜視覺錘庫，回傳頁面 URL"""
    visual_data = analysis.get("視覺錘分析", {})
    visual_hammer_type = analysis.get("視覺錘類型", "") or _get_nested(visual_data, "視覺錘是什麼", "")
    visual_analysis = _get_nested(visual_data, "視覺錘是什麼") or str(visual_data) if visual_data else ""
    language_nail = _get_nested(visual_data, "語言釘是什麼", "")
    industry_data = analysis.get("產業適用性分析", {})
    industry = _get_nested(industry_data, "原始產業") or "通用"

    if not visual_analysis:
        return ""

    content = f"""# {visual_hammer_type}｜{industry}

**視覺錘類型**：{visual_hammer_type}
**視覺錘分析**：{visual_analysis}
**語言釘**：{language_nail}
**適用產業**：{industry}
**來源影片**：{source_url}
"""
    res = call_notion_mcp("notion-create-pages", {
        "data_source_id": NOTION_DB_IDS["視覺錘庫"],
        "pages": [{"content": content}]
    }, db_key="視覺錘庫")

    page_url = res.get("url", "")
    if res.get("success"):
        print(f"[✓] 視覺錘已寫入 06｜視覺錘庫：{visual_hammer_type} → {page_url}")
    else:
        print(f"[✗] 06 庫寫入失敗：{res.get('error', '')}")
    return page_url


def write_to_language_nail_library(analysis: dict, source_url: str) -> str:
    """將語言釘公式寫入 07｜語言釘庫，回傳頁面 URL"""
    visual_data = analysis.get("視覺錘分析", {})
    language_nail = _get_nested(visual_data, "語言釘是什麼", "")
    industry_data = analysis.get("產業適用性分析", {})
    industry = _get_nested(industry_data, "原始產業") or "通用"

    # Fallback：沒有語言釘時，改用鉤子語句或影片標題作為語言釘
    if not language_nail:
        hook_data = analysis.get("鉤子分析", {})
        hook_sentence = _get_nested(hook_data, "鉤子原句", "")
        if hook_sentence:
            language_nail = hook_sentence[:50]  # 鉤子句取前50字
        else:
            title = analysis.get("影片標題或主題", "")
            if title:
                language_nail = title[:50]
            else:
                return ""  # 完全沒有可用內容，才跳過

    content = f"""# {industry}｜語言釘公式

**語言釘**：{language_nail}
**適用產業**：{industry}
**來源影片**：{source_url}
"""
    res = call_notion_mcp("notion-create-pages", {
        "data_source_id": NOTION_DB_IDS["語言釘庫"],
        "pages": [{"content": content}]
    }, db_key="語言釘庫")

    page_url = res.get("url", "")
    if res.get("success"):
        print(f"[✓] 語言釘已寫入 07｜語言釘庫：{language_nail[:30]} → {page_url}")
    else:
        print(f"[✗] 07 庫寫入失敗：{res.get('error', '')}")
    return page_url


def write_to_script_library(analysis: dict, score_result: dict, source_url: str) -> str:
    """將高分影片（4-5分）的腳本寫入 35｜已驗證熱門腳本庫，回傳頁面 URL"""
    content_type = score_result.get("content_type", "IP型")
    db_key = "IP型腳本庫" if content_type == "IP型" else "導購型腳本庫"
    db_id = NOTION_DB_IDS[db_key]

    score_label = score_result.get("score_label", "4分高")
    industry_data = analysis.get("產業適用性分析", {})
    industry = _get_nested(industry_data, "原始產業") or "通用"
    platform = analysis.get("平台", "") or analysis.get("platform", "")
    transcript = analysis.get("逐字稿", "") or analysis.get("transcript", "")
    structure_data = analysis.get("影片結構拆解", {})
    script_structure = _get_nested(structure_data, "結構公式") or str(structure_data) if structure_data else ""
    hook_data = analysis.get("鉤子類型與設計", {})
    hook_formula = _get_nested(hook_data, "可套用公式") or _get_nested(hook_data, "鉤子公式", "")
    cta_data = analysis.get("CTA設計分析", {})
    cta_design = _get_nested(cta_data, "設計分析") or _get_nested(cta_data, "CTA設計", "")
    ad_data = analysis.get("廣告投放潛力評估", {})
    ad_potential = _get_nested(ad_data, "廣告投放潛力") or analysis.get("廣告投放潛力", "B級小改可投")

    # 組合腳本頁面內容
    title = analysis.get("影片標題或主題", "") or analysis.get("title", "未命名腳本")
    content = f"""# {title}

**評分**：{score_label}
**評分理由**：{score_result.get("score_reason", "")}
**最值得借鏡的點**：{score_result.get("key_strength", "")}

---

## 逐字稿
{transcript[:800]}

---

## 腳本結構
{script_structure}

---

## 鉤子公式
{hook_formula}

## CTA設計
{cta_design}

---

**來源連結**：{source_url}
"""

    # 建立頁面屬性
    properties = {
        "評分": score_label,
        "產業": industry,
        "平台": platform,
        "來源連結": source_url,
        "是否已借鏡": False,
    }

    # 導購型額外加廣告投放潛力
    if content_type == "導購型":
        if ad_potential in ["A級直接可投", "B級小改可投"]:
            properties["廣告投放潛力"] = ad_potential

    res = call_notion_mcp("notion-create-pages", {
        "data_source_id": db_id,
        "pages": [{
            "content": content,
            "properties": properties
        }]
    }, db_key=db_key)

    page_url = res.get("url", "")
    display_name = LIBRARY_DISPLAY.get(db_key, "35｜腳本庫")
    if res.get("success"):
        print(f"[✓] 高分腳本已寫入 {display_name}（{content_type}）：{title[:30]} → {page_url}")
    else:
        print(f"[✗] 35 庫寫入失敗：{res.get('error', '')}")
    return page_url


def distribute_video(analysis: dict, source_url: str = "") -> dict:
    """
    主函數：接收一支影片的完整拆解結果，分配到所有對應庫房
    analysis 是 viral_factory.py 的 GPT 拆解輸出

    回傳：{
        "score_result": {...},
        "library_urls": {
            "03｜開頭鉤子庫": "https://...",
            "04｜結尾呼籲庫": "https://...",
            ...
        }
    }
    """
    title_display = analysis.get("影片標題或主題", "") or analysis.get("title", "未命名")
    print(f"\n[分配開始] {title_display}")

    library_urls = {}

    # 03-07/35 are reusable knowledge assets.  Text-only or otherwise
    # unverified analysis may stay quarantined in the run log, but must not be
    # promoted into these libraries.
    if analysis.get("evidence_status") != "verified":
        return {
            "score_result": {
                "score": None,
                "score_status": "insufficient_evidence",
                "score_label": "未評分",
                "content_type": None,
                "score_reason": "證據未通過發布閘門",
            },
            "library_urls": {},
            "distribution_status": "blocked_by_evidence_gate",
        }

    # Step 1：評分
    hook_data = analysis.get("鉤子類型與設計", {})
    visual_data = analysis.get("視覺錘分析", {})
    structure_data = analysis.get("影片結構拆解", {})
    score_result = analysis.get("_score_result")
    if not isinstance(score_result, dict):
        score_result = score_video({
            "title": analysis.get("影片標題或主題", "") or analysis.get("title", ""),
            "platform": analysis.get("平台", "") or analysis.get("platform", ""),
            "viral_data": analysis.get("爆款數據", "") or analysis.get("viral_data", ""),
            "transcript": analysis.get("逐字稿", "") or analysis.get("transcript", ""),
            "hook": str(hook_data) if hook_data else analysis.get("鉤子類型與設計", ""),
            "visual_hammer": str(visual_data) if visual_data else analysis.get("視覺錘分析", ""),
            "script_structure": str(structure_data) if structure_data else analysis.get("影片結構拆解", ""),
            "source_type": analysis.get("source_type", "有機熱門"),
            "evidence_status": analysis.get("evidence_status"),
        })
    print(f"[評分] {score_result.get('score_label')} | {score_result.get('content_type')} | {score_result.get('score_reason')}")

    # Step 2：每個預期庫都必須回傳可驗證 URL；空字串不是成功。
    failures = []
    for display_name, writer in (
        ("03｜開頭鉤子庫", write_to_hook_library),
        ("04｜結尾呼籲庫", write_to_cta_library),
        ("05｜腳本結構庫", write_to_structure_library),
        ("06｜視覺錘庫", write_to_visual_hammer_library),
        ("07｜語言釘庫", write_to_language_nail_library),
    ):
        try:
            page_url = writer(analysis, source_url)
        except Exception as exc:
            page_url = ""
            failures.append({"library": display_name, "error": f"{type(exc).__name__}: {exc}"})
        if page_url:
            library_urls[display_name] = page_url
        elif not any(row["library"] == display_name for row in failures):
            failures.append({"library": display_name, "error": "missing_verified_page_url"})

    # Step 3：只有高分（4-5分）才寫入 35 號腳本庫
    if is_high_score(score_result):
        url_35 = write_to_script_library(analysis, score_result, source_url)
        if url_35:
            content_type = score_result.get("content_type", "IP型")
            lib_key = "35｜IP型腳本庫" if content_type == "IP型" else "35｜導購型腳本庫"
            library_urls[lib_key] = url_35
            print(f"[✓] 高分影片，已寫入 35｜已驗證熱門腳本庫")
        else:
            print("[✗] 高分影片的 35 庫寫入未獲可驗證頁面 URL")
            failures.append({"library": "35｜已驗證熱門腳本庫", "error": "missing_verified_page_url"})
    else:
        print(f"[跳過] 評分 {score_result.get('score')} 分，不寫入腳本庫")

    return {
        "score_result": score_result,
        "library_urls": library_urls,
        "distribution_status": "completed" if not failures else "partial_failure",
        "distribution_failures": failures,
    }


if __name__ == "__main__":
    # 測試用
    test_analysis = {
        "title": "測試影片：你為什麼廣告一直燒錢",
        "platform": "TikTok",
        "viral_data": "播放量：50萬",
        "transcript": "你知道為什麼你的廣告一直燒錢卻沒效嗎？因為你犯了這個錯誤...",
        "hook_type": "疑問式",
        "hook_formula": "你知道為什麼[痛點]嗎？因為你犯了[錯誤]...",
        "hook_design": "開場直接問觀眾痛點，製造好奇心缺口",
        "visual_hammer_type": "人物型",
        "visual_analysis": "直視鏡頭說話，強烈眼神接觸觸發社交本能",
        "language_nail": "廣告燒錢 = 你犯了這個錯誤",
        "script_structure": "Hook(0-3秒) + 痛點放大(3-20秒) + 解法(20-45秒) + CTA(45-60秒)",
        "cta_type": "留言誘餌",
        "cta_design": "留言「我要」獲取完整攻略",
        "industry": "數位課程",
        "source_type": "有機熱門",
        "ad_potential": "A級直接可投",
        "neuroscience": "好奇心缺口 + 損失厭惡",
    }
    result = distribute_video(test_analysis, "https://www.tiktok.com/test")
    print(f"\n最終評分結果：{json.dumps(result['score_result'], ensure_ascii=False, indent=2)}")
    print(f"\n庫房入庫連結：")
    for lib, url in result['library_urls'].items():
        print(f"  {lib} → {url}")
