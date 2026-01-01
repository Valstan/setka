# Управление VK токенами (UI + API + БД)

## Источники истины

- API: `web/api/token_management.py` (в `main.py` подключён как `/api/tokens`)
- БД: таблица `vk_tokens` (`database/models.py`)
- UI: `/tokens` (`web/templates/tokens.html`)

## Основные endpoints

Базовый префикс: `/api/tokens`

- `GET /` — список токенов
- `GET /{token_name}` — получить токен
- `POST /add` — добавить токен
- `PUT /{token_name}` — обновить токен
- `POST /{token_name}/validate` — валидировать токен
- `POST /validate-all` — валидировать все
- `DELETE /{token_name}` — удалить токен

## Безопасность

- В API ответы возвращают **маскированный** токен (первые символы + `...`) — см. `VKToken.to_dict()`.
- Полные токены хранятся в БД и не должны попадать в git.


