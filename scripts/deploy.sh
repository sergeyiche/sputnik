#!/usr/bin/env bash
# Deploy the production branch to the checkout at APP_DIR.
#
# Called by the post-receive hook in /opt/git/sputnik.git on every push to
# refs/heads/main, and safe to run by hand:
#
#   /opt/oracool/scripts/deploy.sh
#   DEPLOY_REF=<commit> /opt/oracool/scripts/deploy.sh   # rollback
#
# Untracked and ignored files (.env, data/, docker-compose.override.yml)
# are never touched.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/oracool}"
BRANCH="${BRANCH:-main}"
REF="${DEPLOY_REF:-origin/$BRANCH}"

log() {
  printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"
}

cd "$APP_DIR"

# Only one deploy at a time; a queued push is picked up by the next run.
exec 9>/var/lock/sputnik-deploy.lock
if ! flock -n 9; then
  log "another deploy is already running, skipping"
  exit 0
fi

log "deploy start: $APP_DIR ($BRANCH <- $REF)"

git fetch --prune origin
git checkout -f -B "$BRANCH" "$REF"
log "code at $(git rev-parse --short HEAD) $(git log -1 --format=%s)"

# Build sequentially: the API image is heavy and the VPS has little RAM.
log "building api image"
docker compose --profile frontend build api

log "building widget image"
docker compose --profile frontend build widget

log "starting containers"
docker compose --profile frontend up -d --remove-orphans

wait_for() {
  local name="$1" url="$2" attempts="${3:-60}"
  local i
  for i in $(seq 1 "$attempts"); do
    if curl -fsS --max-time 5 "$url" >/dev/null 2>&1; then
      log "$name is up: $url"
      return 0
    fi
    sleep 3
  done
  log "$name did not become healthy: $url"
  return 1
}

api_addr="$(docker compose port api 8000 2>/dev/null | head -n1)"
widget_addr="$(docker compose port widget 80 2>/dev/null | head -n1)"

if ! wait_for api "http://${api_addr:-127.0.0.1:8000}/health"; then
  docker compose --profile frontend ps || true
  docker compose --profile frontend logs --tail=80 api || true
  exit 1
fi

if ! wait_for widget "http://${widget_addr:-127.0.0.1:8080}/widget/sputnik.js" 20; then
  docker compose --profile frontend ps || true
  docker compose --profile frontend logs --tail=80 widget || true
  exit 1
fi

# Old image tags become dangling after a rebuild; keep the disk in check.
docker image prune -f >/dev/null 2>&1 || true

log "deploy done: $(git rev-parse --short HEAD)"
