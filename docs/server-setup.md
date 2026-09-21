# Подготовка VPS

Минимальная конфигурация: **2 vCPU · 4 GB RAM · 40 GB SSD · Ubuntu 22.04/24.04**.

## 1. Docker (один раз)

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
# перелогиньтесь в SSH
docker compose version
```

## 2. Репозиторий

```bash
sudo mkdir -p /www && sudo chown "$USER:$USER" /www
cd /www
git clone <url> oracool
cd oracool
```

## 3. Секреты

```bash
cp .env.example .env
nano .env   # GIGACHAT_AUTHORIZATION_KEY=...
```

При публикации по домену добавьте origin в `CORS_ORIGINS` (для демо за nginx на том же хосте обычно достаточно same-origin через `/v1`).

## 4. Запуск одной командой

```bash
chmod +x scripts/*.sh
./scripts/setup-server.sh
```

Скрипт: проверит Docker → сертификаты НУЦ → `docker compose --profile frontend up` → ingest.

Без переиндексации:

```bash
./scripts/setup-server.sh --skip-ingest
```

## 5. Проверка

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/v1/knowledge/status
# демо: http://SERVER_IP:8080/
```

## 6. Firewall (если включён ufw)

```bash
sudo ufw allow OpenSSH
sudo ufw allow 8080/tcp    # демо + виджет
# sudo ufw allow 8000/tcp  # API напрямую — обычно не нужно
sudo ufw enable
```

## 7. HTTPS (рекомендуется для фидбэка)

Поставьте Caddy или nginx на 443 и проксируйте на `127.0.0.1:8080` (там уже есть `/v1` → API).

После смены домена:

```bash
# при необходимости поправьте CORS_ORIGINS в .env
docker compose --profile frontend up -d
```

## Полезные команды

```bash
docker compose logs -f api widget
./scripts/docker-ingest.sh          # после обновления data/knowledge
docker compose --profile frontend down
```
