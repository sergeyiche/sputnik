#!/usr/bin/env bash
# Создаёт .env для локального запуска API (без Docker).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"

if [[ -f "$ENV_FILE" ]]; then
  echo ".env уже существует: $ENV_FILE"
  exit 0
fi

cp "$ROOT/.env.example" "$ENV_FILE"

# Пути для локального запуска (не Docker)
sed -i \
  -e 's|CHROMA_PERSIST_DIR=/app/data/chroma|CHROMA_PERSIST_DIR=./data/chroma|' \
  -e 's|KNOWLEDGE_DIR=/app/data/knowledge|KNOWLEDGE_DIR=./data/knowledge|' \
  -e 's|KNOWLEDGE_SOURCES_DIR=/app/data/knowledge_sources|KNOWLEDGE_SOURCES_DIR=./data/knowledge_sources|' \
  -e 's|SYSTEM_PROMPT_PATH=/app/config/system_prompt.md|SYSTEM_PROMPT_PATH=./config/system_prompt.md|' \
  "$ENV_FILE"

echo "Создан $ENV_FILE"
echo "Заполните GIGACHAT_AUTHORIZATION_KEY перед запуском ingest и /v1/chat"
echo "(AUTHORIZATION_KEY и GIGACHAT_CREDENTIALS — deprecated aliases)"
