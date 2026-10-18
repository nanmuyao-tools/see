#!/usr/bin/env bash
# 等 ollama 就绪 → 拉模型（首次）→ 运行需求雷达
set -euo pipefail

OLLAMA_API="${OLLAMA_API:-http://ollama:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:7b}"

echo "等待 ollama (${OLLAMA_API}) 就绪 …"
until curl -sf "${OLLAMA_API}/api/tags" >/dev/null 2>&1; do
  sleep 2
done

echo "拉取模型 ${OLLAMA_MODEL}（首次较慢，之后走缓存）…"
curl -sf -X POST "${OLLAMA_API}/api/pull" \
  -d "{\"name\":\"${OLLAMA_MODEL}\"}" >/dev/null

echo "开始运行 …"
exec python -m demand_radar run "$@"
