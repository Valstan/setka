# Документация SETKA (единый вход)

## С чего начать

- Если ты **AI/новая сессия**: начни с [`ai/START_HERE.md`](ai/START_HERE.md)
- Если ты **разработчик**: начни с [`ops/run_local.md`](ops/run_local.md) и [`ops/configuration.md`](ops/configuration.md)

## Карта системы (что где лежит)

### “Источники истины” в коде

- **FastAPI вход**: `main.py` (подключение роутов, UI pages, `/metrics`)
- **API роуты**: `web/api/` (по файлам: `health.py`, `regions.py`, `communities.py`, `posts.py`, `notifications.py`, `token_management.py`, `publisher.py`, `scheduler.py`, `schedule_management.py`, `vk_monitoring.py`, …)
- **Celery**: `celery_app.py` → `tasks/celery_app.py` (расписание `beat_schedule`)
- **Задачи**: `tasks/*.py` (например `correct_workflow_tasks.py`, `publishing_tasks.py`, `notification_tasks.py`)
- **Модели БД**: `database/models.py`
- **Подключение к БД**: `database/connection.py` (требует `DATABASE_URL` из env)
- **Runtime конфиг без секретов**: `config/runtime.py` (читает env: `DATABASE_URL`, `REDIS_URL`, `VK_TOKEN_*`, `TELEGRAM_TOKEN_*`, …)
- **Nginx шаблон для редактирования**: `config/setka.conf.editable` (применение через `scripts/apply_nginx_config.sh`)

### UI

- HTML шаблоны: `web/templates/`
- Статика: `web/static/` (в т.ч. `web/static/js/editor.js` для Quill)

## Архитектура и данные

- Архитектура и потоки: [`architecture/overview.md`](architecture/overview.md)
- Модель данных (таблицы и связи): [`architecture/data_model.md`](architecture/data_model.md)

## Эксплуатация (ops/runbook)

- Конфигурация (env, секреты, токены): [`ops/configuration.md`](ops/configuration.md)
- Запуск локально/на сервере: [`ops/run_local.md`](ops/run_local.md)
- Nginx: [`ops/nginx.md`](ops/nginx.md)
- Мониторинг (Prometheus/Grafana, `/metrics`): [`ops/monitoring.md`](ops/monitoring.md)
- Troubleshooting: [`ops/troubleshooting.md`](ops/troubleshooting.md)

## Функциональные подсистемы (features)

- Управление VK токенами: [`features/token_management.md`](features/token_management.md)
- Уведомления (suggested/messages/comments): [`features/notifications.md`](features/notifications.md)
- Публикация (VK/Telegram/WordPress): [`features/publisher.md`](features/publisher.md)
- Планировщик: [`features/scheduler.md`](features/scheduler.md)
- Сообщества (в т.ч. парсер VK URL): [`features/communities.md`](features/communities.md)
- Визуальный редактор (Quill): [`features/visual_editor.md`](features/visual_editor.md)


