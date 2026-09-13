#!/usr/bin/env bash
# Entrypoint для API-контейнера.
set -euo pipefail

cd /app

# Сертификаты НУЦ Минцифры (нужны для GigaChat OAuth)
if [[ ! -f /app/config/certs/gigachat-ca-bundle.pem ]]; then
  echo "[entrypoint] CA bundle not found — downloading Russian Trusted Root CA..."
  bash /app/scripts/setup-gigachat-certs.sh || {
    echo "[entrypoint] WARNING: failed to download certs. Set GIGACHAT_VERIFY_SSL_CERTS=false for dev only."
  }
fi

# Путь к CA по умолчанию, если не задан явно
if [[ -z "${GIGACHAT_CA_BUNDLE_FILE:-}" && -f /app/config/certs/gigachat-ca-bundle.pem ]]; then
  export GIGACHAT_CA_BUNDLE_FILE=/app/config/certs/gigachat-ca-bundle.pem
fi

# Chroma SQLite + journal/WAL требуют запись в каталог и файл.
# Bind-mount с хоста часто приходит как 644 → "readonly database".
mkdir -p /app/data/chroma
chmod -R a+rwX /app/data/chroma 2>/dev/null || true
if [[ -f /app/data/chroma/chroma.sqlite3 ]]; then
  chmod a+rw /app/data/chroma/chroma.sqlite3 2>/dev/null || true
fi

echo "[entrypoint] chroma dir: $(ls -ld /app/data/chroma 2>/dev/null || true)"
echo "[entrypoint] starting: $*"
exec "$@"
