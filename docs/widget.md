# Виджет чата (встраиваемый)

Плавающий Vue 3-виджет для сайта: кнопка в углу → диалог с RAG-API.

## Быстрый старт (Docker)

```bash
./scripts/docker-frontend.sh
# открыть http://localhost:8080/
```

Демо-страница: `sample-site/index.html` (отдаётся nginx).  
Запросы `/v1/*` и `/health` проксируются на сервис `api` — same-origin, cookies сессии работают без CORS.

## Быстрый старт (локально, без Docker frontend)

Нужен запущенный API (`./scripts/dev-api.sh` или `./scripts/docker-up.sh`):

```bash
./scripts/serve-demo.sh
# → http://localhost:8080/
```

Скрипт соберёт виджет при необходимости и поднимет мини-сервер с прокси `/v1` → `:8000`.

## Сборка артефактов

```bash
cd apps/widget
npm install
npm run build
# → dist/parkinson-chat-widget.js
# → dist/parkinson-chat-widget.css
```

## Встраивание на сайт

```html
<link rel="stylesheet" href="https://your.cdn/widget/parkinson-chat-widget.css" />
<script src="https://your.cdn/widget/parkinson-chat-widget.js" defer></script>
<parkinson-chat-widget api-url="https://your-api.example.com"></parkinson-chat-widget>
```

| Атрибут | По умолчанию | Описание |
|---------|--------------|----------|
| `api-url` | `""` (= `window.location.origin`) | Базовый URL API без слэша в конце |
| `title` | «Ассистент по болезни Паркинсона» | Заголовок панели |
| `placeholder` | … | Placeholder поля ввода |

Программный mount:

```js
window.ParkinsonChatWidget.mount(document.querySelector('#chat'), {
  apiUrl: 'https://api.example.com',
})
```

## Локальная разработка

```bash
./scripts/dev-api.sh          # API :8000
./scripts/dev-widget.sh       # Vite :5173, VITE_API_URL → API
```

В dev пустой `api-url` подменяется на `http://localhost:8000` (см. `FloatingChat.vue`).

## Архитектура

- Entry prod: `src/embed.ts` → IIFE `parkinson-chat-widget.js`
- Entry dev: `src/main.ts` + `index.html`
- UI: `src/components/FloatingChat.vue`
- API: `POST /v1/chat` (`stream: false`), `DELETE /v1/session`, `credentials: 'include'`
