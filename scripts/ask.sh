#!/usr/bin/env bash
# Удобный запрос к чату с читаемым выводом в терминале.
# Пример: ./scripts/ask.sh "Что такое болезнь Паркинсона?"
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
COOKIE_JAR="${COOKIE_JAR:-/tmp/parkinson-session.txt}"
MESSAGE="${1:-Что такое болезнь Паркинсона?}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${ROOT}/.venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON=python3

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

HTTP_CODE="$(curl -sS -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
  -X POST "$BASE_URL/v1/chat" \
  -H "Content-Type: application/json; charset=utf-8" \
  -o "$TMP" -w "%{http_code}" \
  -d "$("$PYTHON" -c "import json,sys; print(json.dumps({'message': sys.argv[1], 'stream': False}, ensure_ascii=False))" "$MESSAGE")")"

echo "HTTP $HTTP_CODE"
echo

"$PYTHON" - <<PY
import json
from pathlib import Path

raw = Path("$TMP").read_text(encoding="utf-8")
if not raw.strip():
    raise SystemExit("Пустой ответ от API")

data = json.loads(raw)
print("session:", data.get("session_id"))
print("sources:", [s.get("source") for s in data.get("sources", [])])
print()
print(data.get("answer", ""))
PY
