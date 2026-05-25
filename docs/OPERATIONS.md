# Ops / эксплуатация SETKA

## 1) Быстрый запуск и проверка

```bash
cd /home/valstan/SETKA
source venv/bin/activate
python main.py
```

Проверка:

```bash
curl http://127.0.0.1:8000/api/health/
```

Swagger: `http://127.0.0.1:8000/docs`

## 2) Systemd (production)

Сервисы:

- `setka`
- `setka-celery-worker`
- `setka-celery-beat`

Перезапуск:

```bash
sudo systemctl restart setka setka-celery-worker setka-celery-beat
```

Логи: `/home/valstan/SETKA/logs/` (есть logrotate).

## 3) Celery (ручной запуск)

```bash
cd /home/valstan/SETKA
./scripts/start_celery.sh
```

Остановка:

```bash
./scripts/stop_celery.sh
```

Расписания: `tasks/celery_app.py` → `app.conf.beat_schedule`.

## 4) Конфигурация и env

Принцип: секреты не в репозитории. Читаются из env (см. `config/runtime.py`, `database/connection.py`).

На VPS env хранится в `/etc/setka/setka.env`.

Обязательные переменные:

- `DATABASE_URL` (async SQLAlchemy)
- `REDIS_URL`

Часто используемые:

- `VK_TOKEN_<NAME>`
- `TELEGRAM_TOKEN_<NAME>`
- `TELEGRAM_ALERT_CHAT_ID`
- `GROQ_API_KEY` (опционально)
- `SERVER_HOST`, `SERVER_PORT`
- `LOG_LEVEL` (по умолчанию `WARNING`)

## 5) Nginx

Редактируемая копия: `config/setka.conf.editable`.

Применение:

```bash
/home/valstan/SETKA/scripts/apply_nginx_config.sh
```

Что должно работать:

- `:80` → редирект на `:443`
- `:443` → прокси на `127.0.0.1:8000`
- `/static` → alias на `web/static`

## 6) Мониторинг

- Метрики: `GET /metrics`
- Prometheus config: `config/prometheus.yml`
- Grafana подключается к Prometheus

## 7) Troubleshooting (коротко)

Сводка состояния:

```bash
cd /home/valstan/SETKA
bash scripts/check-setka.sh
```

Диагностика:

```bash
bash scripts/diagnose_connection.sh
```

Если FastAPI не отвечает:

```bash
ps aux | grep uvicorn
tail -n 200 logs/uvicorn_production.log
curl http://127.0.0.1:8000/api/health/
```

## 8) Hot-fix runbook — branch protection

С 2026-05-23 на `main` GitHub-репо включена branch protection ([ADR-0002](../../brain_matrica/adr/0002-pr-only-flow-no-direct-push.md) §D):

- PR required (0 approvals; self-merge OK)
- CI `test (3.12)` required + strict (ветка должна быть свежей)
- force-push в `main` запрещён
- удаление `main` запрещено
- enforce_admins = true (даже owner не может обходить случайно)

**Обычный flow никогда не упирается в защиту** — feature-ветка → PR → CI зелёный → squash-merge.

### Если прод 502 и нужен срочный hot-fix без CI

ADR-0002 §8 даёт лазейку для аварий: разово снять protection, запушить fix, **сразу** вернуть protection и завести follow-up PR постфактум для audit trail.

```bash
# 1. Снять protection (требует gh + admin token)
gh api -X DELETE repos/Valstan/setka/branches/main/protection

# 2. Зафиксить и запушить напрямую в main
git checkout main
# ... правка ...
git commit -m "hotfix: ..."
git push origin main

# 3. Сразу вернуть protection (тот же JSON, что был при включении)
gh api -X PUT repos/Valstan/setka/branches/main/protection --input scripts/branch-protection.json

# 4. Завести follow-up PR с описанием инцидента
gh issue create --title "post-mortem: <инцидент>" --body "..."
```

Шаг 3 — **не забыть**. Если protection не вернётся, защита будет потеряна молча.

JSON конфиг лежит в `scripts/branch-protection.json` (см. репо). Проверить текущее состояние: `gh api repos/Valstan/setka/branches/main/protection`.

### Если CI красный из-за стороннего фейла (не своего кода)

Например, упал GitHub Actions runner, флакающий тест, временный 502 при `pip install`. Варианты:

1. **Перегнать CI** — в PR кнопка «Re-run failed jobs». Самый частый случай.
2. **Дождаться** и попробовать ещё раз через 5-10 минут.
3. **В крайнем случае** — то же что hot-fix runbook, но обязательно follow-up PR с описанием «почему обошли CI».

Если флакающий тест повторяется — это уже техдолг, в `PENDING_FOLLOWUPS.md`.
