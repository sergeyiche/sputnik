#!/usr/bin/env bash
# Конвертация сырых файлов (pdf, docx, xlsx, txt, md) → data/knowledge/*.txt
# Примеры:
#   ./scripts/convert-sources.sh
#   ./scripts/convert-sources.sh --force
#   ./scripts/convert-sources.sh "data/knowledge_sources/Список врачей.xlsx"
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
