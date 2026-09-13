#!/usr/bin/env bash
# Создаёт виртуальное окружение .venv и устанавливает зависимости API.
# Обходит ошибку "externally-managed-environment" на Ubuntu/Debian.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv"
REQUIREMENTS="$ROOT/apps/api/requirements.txt"

cd "$ROOT"

if [[ ! -f "$REQUIREMENTS" ]]; then
  echo "Не найден $REQUIREMENTS"
  exit 1
fi

if [[ -d "$VENV/bin" ]] && [[ -x "$VENV/bin/pip" ]]; then
  echo "venv уже настроен: $VENV"
else
  echo "Создаём venv: $VENV"

  # На Ubuntu без python3-venv ensurepip недоступен — создаём venv без pip
  if python3 -m venv --help 2>&1 | grep -q 'without-pip'; then
    rm -rf "$VENV"
    python3 -m venv "$VENV" --without-pip

    echo "Устанавливаем pip в venv..."
    curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip-parkinson.py
    "$VENV/bin/python" /tmp/get-pip-parkinson.py
    rm -f /tmp/get-pip-parkinson.py
  else
    rm -rf "$VENV"
    python3 -m venv "$VENV"
  fi
fi

echo "Устанавливаем зависимости..."
"$VENV/bin/pip" install -r "$REQUIREMENTS"

echo ""
echo "✓ Готово. Активируйте окружение:"
echo "  source .venv/bin/activate"
echo ""
echo "Или запускайте скрипты напрямую — они используют .venv/bin/python автоматически."
