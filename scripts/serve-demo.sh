#!/usr/bin/env bash
# Локальная демо-страница: sample-site + собранный виджет + прокси /v1 → API.
# Требует: Node 20+, запущенный API на :8000 (./scripts/dev-api.sh или docker-up).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WIDGET_DIR="$ROOT/apps/widget"
PORT="${DEMO_PORT:-8080}"
API_URL="${API_URL:-http://127.0.0.1:8000}"

if ! command -v node >/dev/null 2>&1; then
  echo "Нужен Node.js / npm (Node 20+)."
  exit 1
fi

cd "$WIDGET_DIR"
if [[ ! -d node_modules ]]; then
  npm install
fi
if [[ ! -f dist/parkinson-chat-widget.js ]]; then
  npm run build
fi

export DEMO_PORT="$PORT"
export API_URL="$API_URL"
export SAMPLE_DIR="$ROOT/sample-site"
export WIDGET_DIST="$WIDGET_DIR/dist"

echo "Демо: http://localhost:${PORT}/  (API → ${API_URL})"
exec node <<'NODE'
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const { URL } = require('node:url')

const port = Number(process.env.DEMO_PORT || 8080)
const apiBase = process.env.API_URL || 'http://127.0.0.1:8000'
const sampleDir = process.env.SAMPLE_DIR
const widgetDist = process.env.WIDGET_DIST

const mime = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.json': 'application/json',
}

function sendFile(res, filePath) {
  const ext = path.extname(filePath)
  res.writeHead(200, { 'Content-Type': mime[ext] || 'application/octet-stream' })
  fs.createReadStream(filePath).pipe(res)
}

function proxy(req, res) {
  const target = new URL(req.url, apiBase)
  const headers = { ...req.headers, host: target.host }
  const opts = {
    protocol: target.protocol,
    hostname: target.hostname,
    port: target.port || (target.protocol === 'https:' ? 443 : 80),
    path: target.pathname + target.search,
    method: req.method,
    headers,
  }
  const upstream = http.request(opts, (up) => {
    res.writeHead(up.statusCode || 502, up.headers)
    up.pipe(res)
  })
  upstream.on('error', (err) => {
    res.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' })
    res.end(`Bad gateway to API (${apiBase}): ${err.message}`)
  })
  req.pipe(upstream)
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url || '/', `http://127.0.0.1:${port}`)
  if (url.pathname.startsWith('/v1/') || url.pathname === '/health') {
    return proxy(req, res)
  }
  if (url.pathname.startsWith('/widget/')) {
    const name = path.basename(url.pathname)
    const filePath = path.join(widgetDist, name)
    if (!filePath.startsWith(widgetDist) || !fs.existsSync(filePath)) {
      res.writeHead(404)
      return res.end('Not found')
    }
    return sendFile(res, filePath)
  }
  let rel = url.pathname === '/' ? 'index.html' : url.pathname.replace(/^\//, '')
  const filePath = path.join(sampleDir, rel)
  if (!filePath.startsWith(sampleDir) || !fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
    res.writeHead(404)
    return res.end('Not found')
  }
  sendFile(res, filePath)
})

server.listen(port, '0.0.0.0', () => {
  console.log(`listening on http://0.0.0.0:${port}`)
})
NODE
