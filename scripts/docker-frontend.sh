#!/usr/bin/env bash
# Сборка и запуск демо-страницы + виджета (nginx) через Docker.
# API должен быть доступен; при необходимости поднимается вместе.
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

echo "==> docker compose --profile frontend up -d --build"
docker compose --profile frontend up -d --build

PORT="${WIDGET_PORT:-8080}"
echo ""
echo "Демо:    http://localhost:${PORT}/"
echo "Виджет:  http://localhost:${PORT}/widget/parkinson-chat-widget.js"
echo "API через nginx: http://localhost:${PORT}/health"
echo ""
echo "Индексация (если ещё не делали):"
echo "  ./scripts/docker-ingest.sh"
echo "Логи:"
echo "  docker compose logs -f widget api"
