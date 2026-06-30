#!/bin/bash
# 載入 viral-factory .env 環境變數
# 用法：source /home/ubuntu/viral_factory/load_env.sh

ENV_FILE="/home/ubuntu/viral_factory/.env"

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
