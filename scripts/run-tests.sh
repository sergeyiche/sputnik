#!/usr/bin/env bash
# Запуск функциональных тестов.
# Требует: .venv, .env с GIGACHAT_AUTHORIZATION_KEY, проиндексированную базу знаний.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  bash "$ROOT/scripts/setup-venv.sh"
fi

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

ARGS=("$@")
if [[ ${#ARGS[@]} -eq 0 ]]; then
  ARGS=(-m functional)
fi

exec "$ROOT/.venv/bin/python" -m pytest "${ARGS[@]}"
