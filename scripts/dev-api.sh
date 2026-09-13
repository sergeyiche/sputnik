#!/usr/bin/env bash
# Локальный запуск API для разработки и curl-тестов.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "venv не найден. Запускаю setup-venv.sh ..."
  bash "$ROOT/scripts/setup-venv.sh"
fi

PYTHON="$ROOT/.venv/bin/python"

if [[ ! -f .env ]]; then
  bash "$ROOT/scripts/setup-local-env.sh"
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

HOST="${API_HOST:-0.0.0.0}"
PORT="${API_PORT:-8000}"

echo "API: http://${HOST}:${PORT}"
echo "Docs: http://localhost:${PORT}/docs"
echo ""

exec "$PYTHON" -m uvicorn apps.api.app.main:app --host "$HOST" --port "$PORT" --reload
