# Публикация (VK / Telegram / WordPress)

## Источники истины

- API: `web/api/publisher.py` (в `main.py` подключён как `/api/publisher`)
- Модули: `modules/publisher/*`
- Celery: `tasks/publishing_tasks.py` (публикация по расписанию/вручную)

## Режимы публикации

В `web/api/publisher.py` используется `publish_mode`:
- `test` — публикация в тестовую группу (`VK_TEST_GROUP_ID`)
- `production` — публикация в группы региона (`VK_PRODUCTION_GROUPS`)

Значения берутся из `config/runtime.py` (env).

## Основные endpoints

Базовый префикс: `/api/publisher`

- `GET /groups`
- `GET /status`
- `POST /publish/simple`
- `POST /publish/region`
- `POST /publish/custom`
- `GET /regions/{region_code}/posts`


