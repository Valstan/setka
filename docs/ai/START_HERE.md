# START HERE (для AI и новых сессий)

## 1) Что это за проект (коротко)

**SETKA** — FastAPI-сервис + Celery-задачи для сбора/обработки контента (VK), хранения в PostgreSQL, выдачи через Web UI/REST API и вспомогательных автоматизаций (уведомления, публикация, метрики).

## 2) Самые важные файлы (источники истины)

- `main.py` — FastAPI приложение: роуты API, страницы UI, `/metrics`
- `tasks/celery_app.py` — **расписание** задач (Celery beat)
- `web/api/*` — конкретные endpoints (истина для API)
- `database/models.py` — структура таблиц
- `config/runtime.py` — runtime config (env-only, без секретов в git)

## 3) Быстрый запуск “для проверки”

### Предусловия (env)

Обязательные переменные окружения:

- `DATABASE_URL` (пример: `postgresql+asyncpg://user:pass@localhost:5432/setka`)
- `REDIS_URL` (пример: `redis://localhost:6379/0`)

Опциональные, но часто нужны:

- `VK_TOKEN_<NAME>` (пример: `VK_TOKEN_VALSTAN=...`)
- `TELEGRAM_TOKEN_<NAME>` (пример: `TELEGRAM_TOKEN_VALSTANBOT=...`)
- `TELEGRAM_ALERT_CHAT_ID`
- `SERVER_HOST`, `SERVER_PORT`
- `GROQ_API_KEY` (если используете Groq)

### Запуск FastAPI

```bash
cd /home/valstan/SETKA
source venv/bin/activate
python main.py
```

Проверка:

```bash
curl http://localhost:8000/api/health/
```

Swagger:

- `http://localhost:8000/docs`

### Запуск Celery

```bash
cd /home/valstan/SETKA
source venv/bin/activate
./scripts/start_celery.sh
```

Остановка:

```bash
./scripts/stop_celery.sh
```

## 4) Что реально есть (ориентиры)

- **UI (templates)**: `/`, `/regions`, `/posts`, `/communities`, `/notifications`, `/tokens`, `/publisher`, `/monitoring`, `/schedule`
- **Уведомления**: `web/api/notifications.py` + `modules/notifications/*` + Redis storage
- **Token management**: `web/api/token_management.py` + `vk_tokens`
- **Метрики**: `/metrics`

## 5) Что часто рассинхронизируется

- **Расписания Celery**: документировать по `tasks/celery_app.py` (beat_schedule)
- **Секреты**: документировать по env (`config/runtime.py`, `database/connection.py`), не по “secure” файлам


