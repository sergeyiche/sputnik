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
# → dist/sputnik.js
# → dist/sputnik.css
```

## Встраивание на сайт

```html
<link rel="stylesheet" href="https://your.cdn/widget/sputnik.css" />
<script src="https://your.cdn/widget/sputnik.js" defer></script>
<sputnik-widget></sputnik-widget>
```

| Атрибут | По умолчанию | Описание |
|---------|--------------|----------|
| `api-url` | `""` (= `window.location.origin`) | Базовый URL API без слэша в конце |
| `title` | «Спутник» | Заголовок, пока не загрузился `GET /v1/widget/config`. Название и описание шапки задаются в заявке и правятся во вкладке «Виджет» |
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

- Entry prod: `src/embed.ts` → IIFE `sputnik.js`
- Entry dev: `src/main.ts` + `index.html`
- UI: `src/components/FloatingChat.vue`
- API: `POST /v1/chat` (`stream: false`), `GET /v1/session`, заголовок `X-Session-Id`
- Идентификатор диалога хранится в `localStorage` (`sputnik_session`). После обновления страницы виджет забирает реплики через `GET /v1/session`. Сервер пишет переписку в SQLite без срока давности.
- «Очистить» выпускает новый идентификатор и начинает пустой диалог. Прежняя переписка остаётся во вкладке «Сессии» кабинета организации.
