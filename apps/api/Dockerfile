FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/app/.cache/huggingface \
    TRANSFORMERS_CACHE=/app/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/app/.cache/torch/sentence_transformers

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# CPU torch first — меньше образ, без CUDA
RUN pip install --no-cache-dir \
    torch --index-url https://download.pytorch.org/whl/cpu

COPY apps/api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY apps/api /app/apps/api
COPY packages /app/packages
COPY config /app/config
COPY data/knowledge /app/data/knowledge
COPY data/knowledge_sources /app/data/knowledge_sources
COPY scripts/docker-entrypoint.sh /app/scripts/docker-entrypoint.sh
COPY scripts/setup-gigachat-certs.sh /app/scripts/setup-gigachat-certs.sh

RUN chmod +x /app/scripts/docker-entrypoint.sh /app/scripts/setup-gigachat-certs.sh \
    && mkdir -p /app/data/chroma /app/data/knowledge /app/data/knowledge_sources /app/.cache

# Запускаем от root: mounted volumes (./data/chroma) принадлежат хост-пользователю,
# non-root appuser не может писать в SQLite Chroma → падения Rust/Segment API.
# Для production позже: user namespace / chown entrypoint.

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["uvicorn", "apps.api.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
