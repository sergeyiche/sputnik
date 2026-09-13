#!/usr/bin/env bash
# Скачивает сертификаты НУЦ Минцифры для GigaChat API.
# Docs: https://developers.sber.ru/docs/ru/gigachat/certificates
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CERT_DIR="$ROOT/config/certs"
mkdir -p "$CERT_DIR"

ROOT_CA="$CERT_DIR/russian_trusted_root_ca_pem.crt"
SUB_CA="$CERT_DIR/russian_trusted_sub_ca_pem.crt"
BUNDLE="$CERT_DIR/gigachat-ca-bundle.pem"

echo "Скачиваем сертификаты НУЦ Минцифры..."
curl -fsSL "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt" -o "$ROOT_CA"
curl -fsSL "https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt" -o "$SUB_CA"

# Нормализуем переводы строк (CRLF → LF) — иначе Python/ssl падает с PEM lib
sed -i 's/\r$//' "$ROOT_CA" "$SUB_CA"

{
  cat "$ROOT_CA"
  echo
  cat "$SUB_CA"
  echo
} > "$BUNDLE"

if ! openssl verify -CAfile "$BUNDLE" "$ROOT_CA" >/dev/null 2>&1; then
  echo "Предупреждение: openssl verify не прошёл, но bundle создан."
fi

echo "✓ Сертификаты сохранены:"
echo "  $ROOT_CA"
echo "  $SUB_CA"
echo "  $BUNDLE"
echo ""
echo "Добавьте в .env (опционально — автопоиск config/certs/ тоже работает):"
echo "  GIGACHAT_CA_BUNDLE_FILE=./config/certs/gigachat-ca-bundle.pem"
echo "  GIGACHAT_VERIFY_SSL_CERTS=true"
