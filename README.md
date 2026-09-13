# Oracool — ИИ-ассистент по болезни Паркинсона

Информационный RAG-ассистент с плавающим чатом на сайте. Ответы опираются на базу знаний, а не на «память» модели. **Не заменяет консультацию врача.**

Стек: FastAPI · LangChain · GigaChat · ChromaDB · Vue 3.

---

## Развёртывание (Docker)

### Требования

- Docker + Docker Compose v2+
- Ключ авторизации GigaChat (`GIGACHAT_AUTHORIZATION_KEY` из [GigaChat Studio](https://developers.sber.ru/docs/ru/gigachat/api/reference/rest/gigachat-api))
- Нормализованные тексты базы знаний в `data/knowledge/` (`.txt`)

> Сырые исходники (`data/knowledge_sources/`) **не хранятся в git**. Положите PDF/DOCX локально и сконвертируйте, либо используйте уже подготовленные `.txt` в `data/knowledge/`.

### 1. Клонирование и окружение

```bash
git clone <url-репозитория> oracool
cd oracool

cp .env.example .env
# Заполните GIGACHAT_AUTHORIZATION_KEY в .env
```

### 2. Сертификаты НУЦ (SSL для GigaChat)

```bash
./scripts/setup-gigachat-certs.sh
```

### 3. Запуск API

```bash
./scripts/docker-up.sh
# API: http://localhost:8000
# Health: curl -s http://localhost:8000/health
```

### 4. Индексация базы знаний

Нужно при первом запуске и после обновления файлов в `data/knowledge/`:

```bash
./scripts/docker-ingest.sh
curl -s http://localhost:8000/v1/knowledge/status | python3 -m json.tool
```

Проверка ответа:

```bash
./scripts/ask.sh "Что такое болезнь Паркинсона?"
```

### 5. Демо-сайт с виджетом

```bash
./scripts/docker-frontend.sh
# → http://localhost:8080/
```

| Сервис | URL |
|--------|-----|
| API | http://localhost:8000 |
| Swagger | http://localhost:8000/docs |
| Демо + виджет | http://localhost:8080 |

Остановка:

```bash
docker compose --profile frontend down
```

---

## База знаний

| Путь | В git | Назначение |
|------|-------|------------|
| `data/knowledge_sources/` | нет | Сырые PDF/DOCX/… |
| `data/knowledge/` | да (нормализованные `.txt`) | Вход для ingest |
| `data/chroma/` | нет | Векторный индекс (volume) |

Если есть только сырые файлы:

```bash
# локально (venv) или через Docker-контейнер api
./scripts/convert-sources.sh
./scripts/docker-ingest.sh
```

Подробнее: [`data/knowledge_sources/README.md`](data/knowledge_sources/README.md).

---

## Встраивание виджета

```html
<link rel="stylesheet" href="/widget/parkinson-chat-widget.css" />
<script src="/widget/parkinson-chat-widget.js" defer></script>
<parkinson-chat-widget></parkinson-chat-widget>
```

Пустой `api-url` = same origin (удобно за nginx-прокси к API). Иначе:

```html
<parkinson-chat-widget api-url="https://your-api.example.com"></parkinson-chat-widget>
```

---

## Локальная разработка (без Docker API)

```bash
./scripts/setup-local-env.sh
./scripts/setup-venv.sh
./scripts/setup-gigachat-certs.sh
export PYTHONPATH="$(pwd)"
./scripts/ingest.sh --source ./data/knowledge
./scripts/dev-api.sh
```

Виджет (Vite):

```bash
./scripts/dev-widget.sh          # http://localhost:5173
# или демо с прокси:
./scripts/serve-demo.sh          # http://localhost:8080
```

---

## Документация

| Файл | Содержание |
|------|------------|
| [`docs/docker.md`](docs/docker.md) | Compose, volumes, типичные проблемы |
| [`docs/widget.md`](docs/widget.md) | Сборка и встраивание виджета |
| [`docs/api-testing.md`](docs/api-testing.md) | curl / pytest, авторизация GigaChat |
| [`config/system_prompt.md`](config/system_prompt.md) | Поведение ассистента |

---

## Архитектура (кратко)

```
apps/api          — FastAPI (chat, session, health)
apps/widget       — Vue-виджет (IIFE для встраивания)
packages/*        — RAG, knowledge, ETL, GigaChat, security
config/           — системный промпт, CA-сертификаты
data/knowledge/   — тексты для индексации
sample-site/      — минимальная HTML-демо
scripts/          — setup, docker, ingest, тесты
```
