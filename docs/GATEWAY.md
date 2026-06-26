# VK Gateway — ворота доступа в VK для проектов @valstan

SARAFAN (setka) — внутренняя кухня VK: рабочие токены, клиент, smart-routing с
cooldown, per-token rate-limiter. **VK-шлюз** (`/api/gateway`) даёт другим
проектам read-only доступ к VK по HTTP: проект шлёт задачу → SARAFAN исполняет
её своим токеном (со своего IP, под своим rate-limit) → возвращает JSON.

**Токен наружу не выдаётся** — только результат. Причина не только безопасность:
VK привязывает user-токен к IP выпуска, и чужой проект с нашим токеном со своего
сервера получит `error 5 (access_token was given to another ip address)`. Работает
только модель «исполни и верни».

> **v1 — READ-ONLY.** Постинг/удаление в VK через шлюз не делаются (риск бана
> аккаунта) — отдельный guarded-слой позже.

---

## База и авторизация

| | |
|---|---|
| Base URL (внешний) | `https://<SARAFAN_HOST>` — уточнить актуальный домён (в git домен не хранится, `config/setka.conf.editable` → `server_name`) |
| Base URL (тот же хост) | `http://127.0.0.1:8000` |
| Заголовок авторизации | `X-API-Key: <ключ-проекта>` |
| Ключи | свой на каждый проект, в env `GATEWAY_KEY_<PROJECT>` (только на VPS, `/etc/setka/setka.env`) |

Без валидного `X-API-Key` → `401`. Ключ запрашивается у владельца SARAFAN; в
логах фигурирует только имя проекта, не секрет.

---

## Эндпоинты

### `POST /api/gateway/call` — универсальная дверь
Исполнить любой read-метод VK из allowlist.

```json
{ "method": "wall.get", "params": { "owner_id": -1, "count": 5 } }
```

### `GET /api/gateway/community?group=<id|screen_name>&fields=<csv>`
Инфо о сообществе (`groups.getById`). `fields` опционально (дефолт — описание,
число подписчиков, активность, статус, screen_name, фото, сайт, контакты).

### `GET /api/gateway/wall?owner_id=<-id>&count=<1..100>&offset=<n>`
Последние посты со стены (`wall.get`). Сообщество — `owner_id` со знаком минус.

---

## Формат ответа

Успех:
```json
{ "ok": true, "response": <сырой VK-payload метода> }
```

VK вернул доменную ошибку (закрытая стена, удалённый объект и т.п.):
```json
{ "ok": false, "error": { "error_code": 15, "error_msg": "Access denied" } }
```

HTTP-коды: `401` (нет/неверный ключ), `400` (метод вне allowlist), `429`
(квота, см. ниже), `503` (шлюз выключен / нет живого токена).

---

## Разрешённые методы (`GATEWAY_READ_METHODS`)

`groups.getById`, `groups.getMembers`, `groups.search`, `groups.isMember`,
`wall.get`, `wall.getById`, `wall.getComments`, `wall.getReposts`,
`users.get`, `users.getFollowers`, `users.getSubscriptions`,
`likes.getList`, `board.getTopics`, `board.getComments`,
`photos.get`, `photos.getAlbums`, `video.get`,
`utils.resolveScreenName`, `database.getCities`, `database.getCountries`,
`newsfeed.search`, `stats.getPostReach`.

Запись (`wall.post/edit/delete`, `messages.send`, `likes.add`, `wall.repost`)
запрещена → `400`.

---

## Квоты и rate-limit

- **На ключ:** `GATEWAY_QUOTA_PER_MIN` (дефолт 30) и `GATEWAY_QUOTA_PER_DAY`
  (дефолт 5000). Превышение → `429` + заголовок `Retry-After: <сек>`.
- **Самозащита токена** (прозрачна для потребителя): per-token rate-limiter
  (~2.5 запроса/с) + авто-cooldown токена при VK error 5/17/29.
- **Глобально:** общий per-IP лимит приложения ~100 запросов/мин.

Эти лимиты держат VK-аккаунт SARAFAN от блокировок — потребителю достаточно
обрабатывать `429`/`Retry-After`.

---

## Примеры (curl)

```bash
KEY=...   # из /etc/setka/setka.env, GATEWAY_KEY_<PROJECT>

# инфо о сообществе
curl -s -H "X-API-Key: $KEY" \
  "https://<SARAFAN_HOST>/api/gateway/community?group=apiclub"

# последние 5 постов со стены сообщества id=1
curl -s -H "X-API-Key: $KEY" \
  "https://<SARAFAN_HOST>/api/gateway/wall?owner_id=-1&count=5"

# универсальный вызов
curl -s -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"method":"users.get","params":{"user_ids":"1,2","fields":"city,bdate"}}' \
  "https://<SARAFAN_HOST>/api/gateway/call"
```

Для AI-сессии другого проекта: «возьми VK-данные через шлюз SARAFAN» = один
`curl` с `X-API-Key`. SDK не нужен.

---

## Эксплуатация

- Аварийный kill-switch: env `GATEWAY_DISABLED=1` → весь шлюз отдаёт `503`
  (токены и публикации SARAFAN не затрагиваются).
- Реализация: роутер `web/api/gateway.py`, квота `modules/gateway/quota.py`,
  конфиг/allowlist `config/gateway.py`. Переиспользует `TokenPolicy`
  (`modules/vk_token_router.py`) и `VKClient` (`modules/vk_monitor/vk_client.py`).
- Деплой: без миграции БД — добавить `GATEWAY_KEY_<PROJECT>` в
  `/etc/setka/setka.env` → restart web.
