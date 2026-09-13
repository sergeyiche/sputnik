#!/usr/bin/env bash
# Индексация базы знаний внутри Docker (пересоздаёт коллекцию Chroma).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> ensure api image exists"
docker compose build api

echo "==> convert sources (на хосте, если есть scripts)"
if [[ -x "$ROOT/scripts/convert-sources.sh" && -x "$ROOT/.venv/bin/python" ]]; then
  bash "$ROOT/scripts/convert-sources.sh" || true
fi

echo "==> ingest in container (volume chroma_data)"
docker compose --profile tools run --rm ingest

echo ""
echo "Проверка:"
echo "  curl -s http://localhost:${API_PORT:-8000}/v1/knowledge/status | python3 -m json.tool"
