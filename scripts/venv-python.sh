#!/usr/bin/env bash
# Возвращает путь к Python из .venv или подсказку по установке.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$ROOT/.venv/bin/python"

if [[ -x "$VENV_PYTHON" ]]; then
  echo "$VENV_PYTHON"
  exit 0
fi

echo "venv не найден. Запустите: ./scripts/setup-venv.sh" >&2
exit 1
