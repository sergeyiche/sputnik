#!/usr/bin/env bash
# Локальная разработка виджета (Vite HMR). API — отдельно на :8000.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/apps/widget"

if ! command -v npm >/dev/null 2>&1; then
  echo "Нужен Node.js / npm. Установите Node 20+ и повторите."
  exit 1
fi

if [[ ! -d node_modules ]]; then
  npm install
fi

export VITE_API_URL="${VITE_API_URL:-http://localhost:8000}"
echo "Dev-виджет → http://localhost:5173 (API: ${VITE_API_URL})"
npm run dev -- --host 0.0.0.0 --port 5173
