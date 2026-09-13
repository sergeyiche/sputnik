#!/usr/bin/env bash
# Индексация базы знаний в ChromaDB (использует .venv, не системный python3).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "venv не найден. Запускаю setup-venv.sh ..."
  bash "$ROOT/scripts/setup-venv.sh"
fi

if [[ ! -f "$ROOT/config/certs/gigachat-ca-bundle.pem" ]]; then
  echo "Сертификаты GigaChat не найдены. Запускаю setup-gigachat-certs.sh ..."
  bash "$ROOT/scripts/setup-gigachat-certs.sh"
fi

if [[ -f "$ROOT/.env" ]]; then
  _PRESERVED_EMBEDDINGS="${EMBEDDINGS_PROVIDER:-}"
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
  if [[ -n "$_PRESERVED_EMBEDDINGS" ]]; then
    export EMBEDDINGS_PROVIDER="$_PRESERVED_EMBEDDINGS"
  fi
fi

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

exec "$ROOT/.venv/bin/python" -m packages.etl.ingest "$@"
