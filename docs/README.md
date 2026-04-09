# Документация SETKA (единый вход)

## С чего начать

- **AI/новая сессия**: начни с [`START_HERE.md`](START_HERE.md)
- **Руководство AI-разработчика**: [`AI_DEV_GUIDE.md`](AI_DEV_GUIDE.md)
- **Разработка и ops**: [`OPERATIONS.md`](OPERATIONS.md)

## Правило актуальности

Если документация расходится с кодом — верить коду и настройкам сервиса. Основные источники истины:

- `main.py`, `web/api/*`
- `tasks/celery_app.py`, `tasks/*.py`
- `database/models.py`, `database/connection.py`
- `config/runtime.py`

## Карта документации

### Для разработки

| Документ | Описание |
|----------|----------|
| [`AI_DEV_GUIDE.md`](AI_DEV_GUIDE.md) | 📘 Полное руководство для AI-разработчиков (архитектура, безопасность, типизация, self-review) |
| [`TESTING.md`](TESTING.md) | 🧪 Тестирование: unit-тесты, интеграционные тесты, CI/CD, как писать тесты |
| [`DEV_HISTORY.md`](DEV_HISTORY.md) | 📜 История всех значимых изменений проекта |
| [`paths.md`](paths.md) | 🗺 Архитектура, API endpoints, потоки данных |

### Для эксплуатации

| Документ | Описание |
|----------|----------|
| [`START_HERE.md`](START_HERE.md) | 🚀 Быстрый старт, команды, сервисы, синхронизация |
| [`OPERATIONS.md`](OPERATIONS.md) | 🔧 Ops/runbook, troubleshooting, Nginx, мониторинг |
| [`DEPLOY.md`](DEPLOY.md) | 🚀 Deployment guide, Celery расписания |
| [`MIGRATION_GUIDE.md`](MIGRATION_GUIDE.md) | 🔄 Миграция old_postopus → SETKA |
| [`MCP_SETUP_VSCODE.md`](MCP_SETUP_VSCODE.md) | 💻 Настройка MCP для VS Code |


