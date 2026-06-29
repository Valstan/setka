"""HTTP-клиент VK-шлюза SARAFAN — ядро MCP-обёртки (consumer-side артефакт).

Тонкая обёртка над публичным контрактом ``docs/GATEWAY.md`` (read-only ворота в
VK). **Не зависит от пакета ``mcp``** — чистая логика запроса/разбора, тестируется
без сети и без MCP SDK (в т.ч. в venv самого SARAFAN). ``server.py`` импортирует
этот клиент и оборачивает его в инструменты FastMCP.

Конфиг — из окружения потребителя:
  SARAFAN_GATEWAY_URL   базовый URL шлюза (дефолт — текущий прод-хост);
  SARAFAN_GATEWAY_KEY   API-ключ проекта (``GATEWAY_KEY_<PROJECT>`` у владельца).
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx

# Текущий прод-хост шлюза (при переезде VPS — обновить, см. docs/GATEWAY.md).
DEFAULT_GATEWAY_URL = "https://3931b3fe50ab.vps.myjino.ru"
DEFAULT_TIMEOUT = 30.0


class GatewayError(Exception):
    """Ошибка шлюза/транспорта с actionable-подсказкой для агента.

    Бросается на не-200 HTTP-кодах (401/400/429/503), отсутствии ключа и
    сетевых сбоях. Доменные VK-ошибки (закрытая стена и т.п.) НЕ ошибка —
    приходят как ``{"ok": false, "error": {...}}`` в теле 200 и возвращаются
    как данные.
    """


def hint_for_status(status: int, retry_after: Optional[str] = None) -> str:
    """Actionable-подсказка по HTTP-коду отказа шлюза (контракт docs/GATEWAY.md)."""
    if status == 401:
        return (
            "401 Unauthorized: ключ не принят. Проверьте переменную окружения "
            "SARAFAN_GATEWAY_KEY (не задана или неверна). Ключ выдаёт владелец SARAFAN."
        )
    if status == 400:
        return (
            "400 Bad Request: метод вне read-allowlist шлюза. Через шлюз доступно "
            "только чтение VK — запись (wall.post/messages.send/…) запрещена."
        )
    if status == 429:
        tail = (
            f" Повторите запрос через {retry_after} с."
            if retry_after
            else " Сбавьте темп запросов."
        )
        return f"429 Too Many Requests: превышена квота шлюза.{tail}"
    if status == 503:
        return (
            "503 Service Unavailable: шлюз временно недоступен (выключен "
            "kill-switch'ем или нет живого VK-токена). Повторите позже."
        )
    return f"{status}: неожиданный ответ шлюза."


def parse_response(status: int, body: Any, retry_after: Optional[str] = None) -> Dict[str, Any]:
    """Привести HTTP-ответ шлюза к payload либо бросить :class:`GatewayError`.

    200 → тело как есть (``{"ok": true, "response": ...}`` или
    ``{"ok": false, "error": {...}}`` — доменная VK-ошибка, это валидные данные).
    Иначе — ``GatewayError`` с подсказкой по коду.
    """
    if status != 200:
        raise GatewayError(hint_for_status(status, retry_after))
    if not isinstance(body, dict):
        raise GatewayError("Неожиданный формат ответа шлюза (ожидался JSON-объект).")
    return body


class GatewayClient:
    """Async-клиент трёх read-эндпоинтов шлюза (`/call`, `/community`, `/wall`)."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("SARAFAN_GATEWAY_URL") or DEFAULT_GATEWAY_URL
        ).rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("SARAFAN_GATEWAY_KEY", "")
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise GatewayError(
                "SARAFAN_GATEWAY_KEY не задан — нечем авторизоваться в шлюзе. "
                "Задайте переменную окружения с ключом проекта."
            )
        return {"X-API-Key": self.api_key}

    async def _request(
        self,
        http_method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        headers = self._headers()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.request(
                http_method, f"{self.base_url}{path}", params=params, json=json, headers=headers
            )
        try:
            body = resp.json()
        except Exception:
            body = None
        return parse_response(resp.status_code, body, resp.headers.get("Retry-After"))

    async def community(self, group: str, fields: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"group": group}
        if fields:
            params["fields"] = fields
        return await self._request("GET", "/api/gateway/community", params=params)

    async def wall(self, owner_id: int, count: int = 20, offset: int = 0) -> Dict[str, Any]:
        return await self._request(
            "GET",
            "/api/gateway/wall",
            params={"owner_id": owner_id, "count": count, "offset": offset},
        )

    async def call(self, vk_method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return await self._request(
            "POST", "/api/gateway/call", json={"method": vk_method, "params": params or {}}
        )
