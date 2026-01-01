# Архитектура SETKA (overview)

## Компоненты

- **FastAPI**: `main.py` + `web/api/*` + `web/templates/*`
- **PostgreSQL**: хранение регионов/сообществ/постов/токенов/фильтров/расписаний
- **Redis**: кеширование (`utils/cache.py`) + хранение уведомлений (`modules/notifications/storage.py`) + Celery broker/backend (по умолчанию)
- **Celery**: фоновые задачи и расписания `tasks/celery_app.py`
- **Nginx**: reverse-proxy к FastAPI, раздача `/static` (см. `config/setka.conf.editable`)
- **Monitoring**: `/metrics` + `config/prometheus.yml`

## Потоки данных (основные)

### 1) Контент-пайплайн (высокоуровнево)

```mermaid
flowchart LR
  Vk[VK_API] --> VkMonitor[modules.vk_monitor]
  VkMonitor --> DbPosts[(PostgreSQL_posts)]
  DbPosts --> Filters[modules.filters]
  Filters --> Scoring[modules.core.scoring]
  Scoring --> Aggregator[modules.aggregation]
  Aggregator --> Publisher[modules.publisher]
  Publisher --> VkOut[VK_Publication]
  Publisher --> TgOut[Telegram_Publication]
```

Где смотреть реализацию:
- `scripts/run_production_workflow.py` (класс `ProductionWorkflow`)
- `tasks/*` (фоновые задачи, расписания см. `tasks/celery_app.py`)

### 2) Уведомления (suggested/messages/comments)

```mermaid
flowchart LR
  CeleryBeat[Celery_beat] --> NotifTasks[tasks_celery_tasks]
  NotifTasks --> Checkers[modules.notifications]
  Checkers --> Redis[(Redis_setka_notifications)]
  Api[web.api.notifications] --> Redis
  Checkers --> Telegram[Telegram_Bot]
```

Истина:
- API: `web/api/notifications.py`
- storage: `modules/notifications/storage.py`
- расписание: `tasks/celery_app.py`

### 3) Token management (VK tokens)

```mermaid
flowchart LR
  User[User_UI] --> ApiTokens[web.api.token_management]
  ApiTokens --> DbTokens[(PostgreSQL_vk_tokens)]
  VkTest[modules.vk_monitor.vk_client] --> ApiTokens
```

## Принцип актуальности

Если “фича есть в доке”, но не подключена в `main.py` и не используется в `tasks/celery_app.py`, это считается **неактуальным**.


