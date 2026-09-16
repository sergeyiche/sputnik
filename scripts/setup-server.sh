#!/usr/bin/env bash
# Подготовка VPS и запуск Oracool (Docker).
# Запуск из корня репозитория: ./scripts/setup-server.sh
#
# Опции:
#   --skip-ingest   не индексировать базу знаний
#   --no-build      up без --build
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SKIP_INGEST=0
COMPOSE_BUILD=(--build)

for arg in "$@"; do
  case "$arg" in
    --skip-ingest) SKIP_INGEST=1 ;;
    --no-build) COMPOSE_BUILD=() ;;
    -h|--help)
      echo "Usage: $0 [--skip-ingest] [--no-build]"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg"
      exit 1
      ;;
  esac
done

echo "==> Проверка Docker"
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker не установлен. На Ubuntu:"
  echo "  curl -fsSL https://get.docker.com | sudo sh"
  echo "  sudo usermod -aG docker \"\$USER\""
  echo "Затем выйдите из SSH и зайдите снова."
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Нужен Docker Compose v2 (plugin: docker compose)."
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Нет доступа к Docker daemon."
  echo "Добавьте пользователя в группу docker и перелогиньтесь:"
  echo "  sudo usermod -aG docker \"\$USER\""
  exit 1
fi

echo "==> .env"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Создан .env из .env.example — заполните GIGACHAT_AUTHORIZATION_KEY и перезапустите."
  exit 1
fi

if ! grep -qE '^GIGACHAT_AUTHORIZATION_KEY=.+' .env; then
  echo "В .env пустой GIGACHAT_AUTHORIZATION_KEY. Заполните ключ и повторите."
  exit 1
fi

echo "==> Сертификаты GigaChat (НУЦ)"
if [[ ! -f config/certs/gigachat-ca-bundle.pem ]]; then
  bash "$ROOT/scripts/setup-gigachat-certs.sh"
else
  echo "    уже есть: config/certs/gigachat-ca-bundle.pem"
fi

if [[ ! -d data/knowledge ]] || [[ -z "$(find data/knowledge -type f \( -name '*.txt' -o -name '*.md' \) 2>/dev/null | head -1)" ]]; then
  echo "В data/knowledge нет текстов. Добавьте .txt/.md или сконвертируйте sources."
  exit 1
fi

echo "==> docker compose --profile frontend up -d ${COMPOSE_BUILD[*]:-}"
docker compose --profile frontend up -d "${COMPOSE_BUILD[@]}"

API_PORT="${API_PORT:-8000}"
WIDGET_PORT="${WIDGET_PORT:-8080}"

echo "==> Ожидание health API"
for i in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then
    echo "    API готов"
    break
  fi
  if [[ "$i" -eq 60 ]]; then
    echo "API не ответил за 60 попыток. Логи: docker compose logs --tail=80 api"
    exit 1
  fi
  sleep 2
done

if [[ "$SKIP_INGEST" -eq 0 ]]; then
  echo "==> Индексация базы знаний (recreate)"
  bash "$ROOT/scripts/docker-ingest.sh"
else
  echo "==> Ingest пропущен (--skip-ingest)"
fi

echo ""
echo "Готово."
echo "  API:     http://127.0.0.1:${API_PORT}/health"
echo "  Демо:    http://127.0.0.1:${WIDGET_PORT}/"
echo "  Status:  curl -s http://127.0.0.1:${API_PORT}/v1/knowledge/status"
echo ""
echo "Откройте порты ${WIDGET_PORT} (и при необходимости ${API_PORT}) в firewall / security group."
echo "Для домена: HTTPS (Caddy/nginx) → proxy на ${WIDGET_PORT}; обновите CORS_ORIGINS в .env."
