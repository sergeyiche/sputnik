# Запуск через Docker Compose

Бэкенд API упакован в Docker. Демо-сайт с виджетом — profile `frontend` (порт 8080).

## Требования

- Docker + Docker Compose v2+
- Файл `.env` с `GIGACHAT_AUTHORIZATION_KEY`
- Сертификаты НУЦ: `./scripts/setup-gigachat-certs.sh` (или entrypoint скачает сам)

## Быстрый старт

```bash
# 1. Ключ и сертификаты
cp -n .env.example .env   # если ещё нет
# заполните GIGACHAT_AUTHORIZATION_KEY
./scripts/setup-gigachat-certs.sh

# 2. Сборка и запуск API
./scripts/docker-up.sh
# или:
# docker compose up -d --build api

# 3. Индексация базы знаний (первый раз / после обновления документов)
./scripts/docker-ingest.sh

# 4. Проверка
curl -s http://localhost:8000/health
curl -s http://localhost:8000/v1/knowledge/status | python3 -m json.tool
./scripts/ask.sh "Что такое болезнь Паркинсона?"
```

## Сервисы

| Сервис | Profile | Назначение |
|--------|---------|------------|
| `api` | (default) | FastAPI + RAG, порт 8000 |
| `ingest` | `tools` | Одноразовая переиндексация Chroma |
| `widget` | `frontend` | nginx: демо + IIFE-виджет, прокси `/v1` → api, порт 8080 |

```bash
docker compose up -d api                          # только API
docker compose --profile tools run --rm ingest    # ingest
./scripts/docker-frontend.sh                      # API + демо с виджетом
# открыть http://localhost:8080/
docker compose logs -f api widget
docker compose down
```

Подробнее про встраивание: [widget.md](./widget.md).

## Volumes

| Путь на хосте | В контейнере | Зачем |
|---------------|--------------|--------|
| `./data/knowledge` | `/app/data/knowledge` | Нормализованные `.txt` |
| `./data/knowledge_sources` | `/app/data/knowledge_sources` | Сырые PDF/DOCX |
| `chroma_data` (named) | `/app/data/chroma` | Векторная БД (отдельный volume — без readonly sqlite) |
| `./config` | `/app/config` | Промпт + CA-сертификаты |
| `hf_cache` (named) | `/app/.cache` | Кеш HuggingFace / embeddings |

> Локальный `./data/chroma` (venv) и Docker `chroma_data` — **разные** хранилища. После первого `docker compose up` обязательно: `./scripts/docker-ingest.sh`.

Пути из `.env` вида `./data/...` **перекрываются** в `docker-compose.yml` на `/app/...`.

## Переменные

Обязательно в `.env`:

```bash
GIGACHAT_AUTHORIZATION_KEY=...
GIGACHAT_MODEL=GigaChat-2
EMBEDDINGS_PROVIDER=local
```

Compose сам выставляет:

- `GIGACHAT_CA_BUNDLE_FILE=/app/config/certs/gigachat-ca-bundle.pem`
- `CHROMA_PERSIST_DIR`, `KNOWLEDGE_DIR`, `SYSTEM_PROMPT_PATH`

## Разработка

| Режим | Команда |
|-------|---------|
| Локально (venv) | `./scripts/dev-api.sh` |
| Docker | `./scripts/docker-up.sh` |

Код `packages/` в runtime-образе **скопирован** при build (не bind-mount). После изменений в Python:

```bash
docker compose up -d --build api
```

## Типичные проблемы

**Первый старт долгий** — качается модель `multilingual-e5-small` в volume `hf_cache`.

**SSL / OAuth** — проверьте наличие `config/certs/gigachat-ca-bundle.pem`.

**`readonly database` / `attempt to write a readonly database`** — Chroma SQLite на bind-mount был не доступен на запись. Сейчас используется named volume `chroma_data`. Пересоздайте стек и прогоните ingest:

```bash
docker compose down
docker compose up -d --build api
./scripts/docker-ingest.sh
```

**`RustBindingsAPI` / `no attribute 'bindings'`** — обычно следствие readonly sqlite (см. выше).
