#!/usr/bin/env bash
# Полный пайплайн: sources → txt → ChromaDB
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

INGEST_ARGS=()
CONVERT_FORCE=false

for arg in "$@"; do
  case "$arg" in
    --recreate) INGEST_ARGS+=("$arg") ;;
    --force) CONVERT_FORCE=true ;;
  esac
done

echo "==> Step 1/2: convert sources to txt"
if [[ "$CONVERT_FORCE" == true ]]; then
  bash "$ROOT/scripts/convert-sources.sh" --force
else
  bash "$ROOT/scripts/convert-sources.sh"
fi

echo ""
echo "==> Step 2/2: ingest into vector store"
bash "$ROOT/scripts/ingest.sh" --source "${KNOWLEDGE_DIR:-./data/knowledge}" "${INGEST_ARGS[@]}"
