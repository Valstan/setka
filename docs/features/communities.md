# Сообщества (Communities) + парсер VK URL

## Источник истины

- API: `web/api/communities.py` (в `main.py` подключён как `/api/communities`)

## Форматы ввода VK сообщества

Поле `vk_id` принимает разные форматы, и сервер преобразует их в корректный отрицательный `vk_id` (owner_id):

- число: `-123456` или `123456`
- URL: `https://vk.com/club160597747`
- URL с параметрами: `https://vk.com/club160597747?search_track_code=...`
- screen name: `vk.com/43admmalmyzh43` или `43admmalmyzh43`

Парсер: функция `extract_vk_id_from_input()` внутри `web/api/communities.py`.

## Основные endpoints

- `GET /api/communities/` — список
- `POST /api/communities/` — добавить


