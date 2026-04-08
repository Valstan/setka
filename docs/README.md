# Документация SETKA (единый вход)

## С чего начать

- **AI/новая сессия**: начни с [`START_HERE.md`](START_HERE.md)
- **Разработка и ops**: [`OPERATIONS.md`](OPERATIONS.md)

## Правило актуальности

Если документация расходится с кодом — верить коду и настройкам сервиса. Основные источники истины:

- `main.py`, `web/api/*`
- `tasks/celery_app.py`, `tasks/*.py`
- `database/models.py`, `database/connection.py`
- `config/runtime.py`

## Карта документации

- Архитектура + пути + API: [`paths.md`](paths.md)
- Ops/runbook: [`OPERATIONS.md`](OPERATIONS.md)


