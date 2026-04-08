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
tail -n 200 logs/app.log
curl http://127.0.0.1:8000/api/health/
```
