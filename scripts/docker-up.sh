#!/usr/bin/env bash
# Сборка и запуск API через Docker Compose.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Нет .env — создаю из .env.example"
  cp .env.example .env
  echo "Заполните GIGACHAT_AUTHORIZATION_KEY в .env и перезапустите."
fi

if [[ ! -f config/certs/gigachat-ca-bundle.pem ]]; then
  echo "Скачиваю сертификаты GigaChat..."
  bash "$ROOT/scripts/setup-gigachat-certs.sh"
fi

echo "==> docker compose build api"
docker compose build api

echo "==> docker compose up -d api"
docker compose up -d api

echo ""
echo "API:   http://localhost:${API_PORT:-8000}"
echo "Docs:  http://localhost:${API_PORT:-8000}/docs"
echo "Health: curl -s http://localhost:${API_PORT:-8000}/health"
echo ""
echo "Индексация (если нужно):"
echo "  ./scripts/docker-ingest.sh"
echo "Логи:"
echo "  docker compose logs -f api"
