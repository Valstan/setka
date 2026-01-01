# Планировщик (Smart Scheduler + расписание публикаций)

## Источники истины

- Smart Scheduler API: `web/api/scheduler.py` (в `main.py` подключён как `/api/scheduler`)
- Управление расписанием: `web/api/schedule_management.py` (в `main.py` подключён как `/api/schedule`)
- Таблица расписания: `publish_schedules` (`database/models.py`)
- Celery расписание фоновых задач: `tasks/celery_app.py`

## Smart Scheduler API

Базовый префикс: `/api/scheduler`

- `GET /optimal-time/{region_code}`
- `GET /engagement-report/{region_code}`
- `GET /should-publish-now/{region_code}`
- `GET /calendar/{region_code}`
- `GET /forecast`
- `POST /schedule`

## Управление расписанием (Schedule Management)

Базовый префикс: `/api/schedule`

- `GET /all`
- `GET /region/{region_code}`
- `POST /add`
- `PUT /update/{schedule_id}`
- `DELETE /delete/{schedule_id}`
- `POST /generate-default`


