# Конфигурация (env, секреты, токены)

## Принцип

**Секреты не храним в репозитории.** Конфигурация берётся из переменных окружения (см. `config/runtime.py` и `database/connection.py`).

## Обязательные переменные окружения

- `DATABASE_URL` — строка подключения Postgres (async SQLAlchemy)\n  Пример: `postgresql+asyncpg://setka_user:***@localhost:5432/setka`
- `REDIS_URL` — строка подключения Redis\n  Пример: `redis://localhost:6379/0`

## VK токены

### Вариант A: через env

Добавляйте токены как переменные с префиксом:
- `VK_TOKEN_VALSTAN=...`
- `VK_TOKEN_VITA=...`

В `config/runtime.py` они соберутся в словарь `VK_TOKENS`.

### Вариант B: через БД (рекомендуется для управления)

Используйте API токен-менеджмента (см. [`../features/token_management.md`](../features/token_management.md)):
- `POST /api/tokens/add`
- `PUT /api/tokens/{name}`
- `POST /api/tokens/{name}/validate`

Таблица: `vk_tokens`.

## Telegram

- `TELEGRAM_TOKEN_<NAME>` (пример: `TELEGRAM_TOKEN_VALSTANBOT=...`)
- `TELEGRAM_ALERT_CHAT_ID`

Где используется:
- уведомления: `web/api/notifications.py` → `checker.send_telegram_notification(...)`

## AI / Groq

- `GROQ_API_KEY` — опционально

## SERVER

Используется для ссылок/хоста:
- `SERVER_HOST` (по умолчанию `127.0.0.1`)
- `SERVER_PORT` (по умолчанию `8000`)

## Timezone и рабочие часы

- Timezone Celery: `config/celery_config.py` (`Europe/Moscow`, `enable_utc = False`)

Текущие ограничения по “рабочим часам” реализованы **в коде задач**:

- Уведомления VK (suggested/messages/comments): обычно 8:00–22:00 MSK\n  См. `tasks/notification_tasks.py` и соответствующие задачи в `tasks/celery_app.py`.
- Основной workflow: 7:00–22:00 MSK\n  См. `tasks/correct_workflow_tasks.py`.


