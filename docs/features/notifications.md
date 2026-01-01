# Уведомления (suggested / messages / comments)

## Источники истины

- API: `web/api/notifications.py` (в `main.py` подключён как `/api/notifications`)
- Хранилище: `modules/notifications/storage.py` (Redis)
- Проверки: `modules/notifications/unified_checker.py` + `vk_*_checker.py`
- Автоматизация: `tasks/celery_app.py` (расписание), `tasks/notification_tasks.py` (task)

## Что мониторится

- **Suggested posts**: предложенные посты в группах
- **Unread messages**: непрочитанные сообщения сообщества
- **Recent comments**: комментарии за последние 24 часа (хранятся в Redis отдельным ключом)

## API

- `GET /api/notifications/` — все уведомления
- `GET /api/notifications/suggested` — suggested
- `GET /api/notifications/messages` — messages
- `GET /api/notifications/comments` — comments
- `POST /api/notifications/check-now` — запустить проверку вручную
- `DELETE /api/notifications/` — очистить

## Где берутся группы

В `check-now` и в задачах используется таблица `regions` и поле `vk_group_id`:
- берутся регионы с `vk_group_id IS NOT NULL`
- некоторые задачи дополнительно фильтруют `is_active == True` и/или `"ИНФО"` в `Region.name` (см. `tasks/celery_app.py`).


