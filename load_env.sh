#!/bin/bash
# 載入 viral-factory .env 環境變數
# 用法：source <專案目錄>/load_env.sh（路徑自動偵測，不需硬編碼）

# 動態計算腳本所在目錄，避免硬編碼 /home/ubuntu 導致環境移植失敗
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key value; do
        # 跳過空行和註解
        [[ -z "$key" || "$key" == \#* ]] && continue
        # 去除前後空格
        key=$(echo "$key" | xargs)
        value=$(echo "$value" | xargs)
        # 只有有值才 export
        if [ -n "$value" ]; then
            export "$key"="$value"
        fi
    done < "$ENV_FILE"
    echo "✅ .env 載入完成"
else
    echo "⚠️  找不到 $ENV_FILE"
fi
