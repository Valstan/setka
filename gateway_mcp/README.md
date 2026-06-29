# VK Gateway MCP — VK-шлюз SARAFAN как MCP-инструменты

MCP-обёртка над read-only [VK-шлюзом SARAFAN](../docs/GATEWAY.md). Даёт
AI-сессии **другого проекта** (@valstan) инструменты «сходить в VK» —
посмотреть сообщество, прочитать стену, вызвать read-метод — без своей
VK-инфраструктуры и без VK-токена. Шлюз исполняет вызов своим токеном (со своего
IP, под своим rate-limit) и возвращает JSON; **токен наружу не уходит**.

> Запускается в среде **потребителя**, не в SARAFAN. SARAFAN лишь раздаёт ключи
> (`GATEWAY_KEY_<PROJECT>`) и хостит сам шлюз.

## Установка

```bash
pip install -r gateway_mcp/requirements.txt
# либо изолированно: pipx / uv venv + pip install mcp httpx
```

## Конфигурация (env)

| Переменная | Обяз. | Назначение |
|---|---|---|
| `SARAFAN_GATEWAY_KEY` | да | API-ключ вашего проекта. Запросите у владельца SARAFAN. |
| `SARAFAN_GATEWAY_URL` | нет | Базовый URL шлюза. Дефолт — текущий прод-хост (см. [GATEWAY.md](../docs/GATEWAY.md)). |

## Подключение к Claude Code / Desktop (`.mcp.json`)

```json
{
  "mcpServers": {
    "vk-gateway": {
      "command": "python",
      "args": ["-m", "gateway_mcp.server"],
      "env": {
        "SARAFAN_GATEWAY_KEY": "<ваш-ключ>",
        "SARAFAN_GATEWAY_URL": "https://3931b3fe50ab.vps.myjino.ru"
      }
    }
  }
}
```

(`command`/`args` — под ваше окружение: можно `uvx`, абсолютный путь к python и т.п.)

## Инструменты

| Инструмент | VK-метод | Назначение |
|---|---|---|
| `vk_get_community(group, fields="")` | `groups.getById` | Инфо о сообществе (id или screen_name). |
| `vk_get_wall(owner_id, count=20, offset=0)` | `wall.get` | Посты со стены (сообщество — `owner_id` со знаком минус). |
| `vk_call(method, params={})` | любой из allowlist | Универсальный read-вызов (users.get, groups.getMembers, …). |

Все инструменты **read-only**. Запись (`wall.post`, `messages.send`, `likes.add`,
`wall.repost`, …) шлюзом запрещена — вернётся `Error: 400`.

### Формат ответа

- Успех: JSON `{"ok": true, "response": <payload VK-метода>}`.
- Доменная VK-ошибка (закрытая стена, удалённый объект): `{"ok": false, "error": {"error_code": .., "error_msg": ".."}}` — это данные, не сбой.
- Отказ транспорта/доступа: строка `Error: <код> <подсказка>` (401 — проверьте ключ; 429 — квота, `Retry-After`; 400 — метод вне allowlist; 503 — шлюз недоступен).

## Архитектура

- [`client.py`](client.py) — async HTTP-клиент шлюза (зависит только от `httpx`; не требует `mcp`; покрыт юнит-тестами в `tests/test_gateway_mcp/`).
- [`server.py`](server.py) — FastMCP-сервер: тонкие `@mcp.tool`-обёртки над клиентом.

Полный контракт шлюза, allowlist методов и квоты — в [`docs/GATEWAY.md`](../docs/GATEWAY.md).
