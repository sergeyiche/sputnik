# Деплой на прод

Прод — сервер `spootnik.online`, каталог `/opt/oracool`, ветка `main`.

## Как это работает

```
локальный git push prod main
        │
        ▼
/opt/git/sputnik.git  (bare-репозиторий на сервере)
        │  hooks/post-receive
        ▼
/opt/oracool/scripts/deploy.sh   (запускается в фоне)
        │
        ├─ git fetch + checkout -f main
        ├─ docker compose build api widget
        ├─ docker compose up -d
        └─ проверка /health и /widget/sputnik.js
```

Хук реагирует только на `refs/heads/main`: push других веток ничего не деплоит.
Деплой идёт в фоне, поэтому `git push` возвращается сразу. Прогресс пишется в
`/var/log/sputnik-deploy.log` на сервере.

`deploy.sh` не трогает неотслеживаемые и игнорируемые файлы: `.env`,
`data/`, `docker-compose.override.yml`, сертификаты остаются на месте.

## Рабочий процесс

```bash
# 1. Работаем в feat/multitenancy, проверяем и коммитим
git switch feat/multitenancy
git add -A && git commit -m "..."

# 2. Переносим в main (main всегда позади, поэтому fast-forward)
git switch main
git merge --ff-only feat/multitenancy

# 3. Отправляем в GitHub и на прод
git push origin main
git push prod main        # ← запускает деплой
```

Если `main` уже был обновлён (`git branch -f main feat/multitenancy`), шаг 2
не нужен.

## Проверка после деплоя

```bash
ssh root@spootnik.online 'tail -n 40 /var/log/sputnik-deploy.log'
ssh root@spootnik.online 'docker compose -f /opt/oracool/docker-compose.yml --profile frontend ps'
ssh root@spootnik.online 'curl -fsS http://127.0.0.1:8000/health'
```

Идёт ли деплой прямо сейчас:

```bash
ssh root@spootnik.online 'flock -n /var/lock/sputnik-deploy.lock true && echo idle || echo running'
```

Скрипт сам ждёт `http://127.0.0.1:8000/health` (до 3 минут) и отдачу
`/widget/sputnik.js`; при ошибке печатает `docker compose ps` и логи
контейнера и завершается с кодом 1.

## Ручной запуск и откат

```bash
# Обычный деплой текущего origin/main
ssh root@spootnik.online /opt/oracool/scripts/deploy.sh

# Откат на конкретный коммит (пересоберёт образы из него)
ssh root@spootnik.online 'DEPLOY_REF=<sha> /opt/oracool/scripts/deploy.sh'
```

Откат не меняет `origin/main`: следующий push в `main` снова задеплоит
актуальную версию.

## Установка хука (один раз)

```bash
ssh root@spootnik.online '
  install -m 755 /opt/oracool/deploy/post-receive /opt/git/sputnik.git/hooks/post-receive &&
  git -C /opt/git/sputnik.git symbolic-ref HEAD refs/heads/main
'
```

## Диагностика

- `fatal: not a git repository` в логе — хук унаследовал `GIT_DIR`
  bare-репозитория. `deploy.sh` сбрасывает `GIT_DIR` и `GIT_WORK_TREE`; если
  ошибка вернулась, проверьте, что `unset GIT_DIR ...` на месте.
- `push` прошёл, а деплой не начался — проверьте, что
  `/opt/git/sputnik.git/hooks/post-receive` существует, исполняемый и что
  push был именно в `main`.
- Деплой пропущен, пока идёт предыдущий (`flock`); изменения подхватит
  следующий push или ручной запуск скрипта.

## Ограничения

- Прод один, без staging: push в `main` сразу уходит на живой сайт.
- Пересборка API занимает несколько минут, если менялся
  `apps/api/requirements.txt`. Изменения только в коде API подхватываются
  перезапуском контейнера (код смонтирован volume-ом), но скрипт всё равно
  пересобирает образ для предсказуемости.
- Во время пересборки старые контейнеры продолжают работать; простой
  начинается только на `up -d`.
