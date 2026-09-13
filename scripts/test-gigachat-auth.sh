#!/usr/bin/env bash
# Проверка OAuth GigaChat без запуска полного API.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Создайте .env и задайте GIGACHAT_AUTHORIZATION_KEY"
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

if [[ ! -f "$ROOT/config/certs/gigachat-ca-bundle.pem" ]]; then
  echo "Сертификаты GigaChat не найдены. Запускаю setup-gigachat-certs.sh ..."
  bash "$ROOT/scripts/setup-gigachat-certs.sh"
fi

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "venv не найден. Запускаю setup-venv.sh ..."
  bash "$ROOT/scripts/setup-venv.sh"
fi

"$ROOT/.venv/bin/python" - <<'PY'
from packages.integrations.gigachat.auth import get_gigachat_auth_service
from packages.integrations.gigachat.client import get_gigachat_client

auth = get_gigachat_auth_service()
token = auth.get_access_token()
print(f"✓ access_token получен, expires_at={token.expires_at}")
print(f"  preview: {token.access_token[:12]}…{token.access_token[-8:]}")

models = get_gigachat_client().list_models()
items = models.get("data", models.get("models", []))
names = [i.get("id") or i.get("name") for i in items if isinstance(i, dict)]
print(f"✓ GET /v1/models — {len(names)} моделей")
for name in names[:5]:
    print(f"  - {name}")
if len(names) > 5:
    print(f"  … и ещё {len(names) - 5}")
PY
