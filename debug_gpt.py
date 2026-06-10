from openai import OpenAI
import json

client = OpenAI()
resp = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "回傳一個簡單的JSON，包含欄位 test: hello"}],
    response_format={"type": "json_object"},
    temperature=0.3
)
print("type:", type(resp))
print("resp:", resp)
print("---")
# 嘗試 model_dump
try:
    d = resp.model_dump()
    print("model_dump:", json.dumps(d, ensure_ascii=False, indent=2)[:500])
except Exception as e:
    print("model_dump error:", e)
