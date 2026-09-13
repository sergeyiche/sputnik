#!/usr/bin/env bash
# Конвертация сырых файлов (pdf, docx, txt, md) → data/knowledge/*.txt
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

exec "$ROOT/.venv/bin/python" -m packages.etl.convert "$@"
