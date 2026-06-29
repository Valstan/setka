#!/usr/bin/env python3
"""MCP-сервер: VK-шлюз SARAFAN как инструменты для AI-сессий проектов @valstan.

Обёртка над read-only HTTP-шлюзом SARAFAN (``docs/GATEWAY.md``). Запускается в
среде ПОТРЕБИТЕЛЯ (не в SARAFAN): даёт его Claude-сессии инструменты «сходить в
VK» без своей VK-инфры. Токен VK наружу не уходит — шлюз исполняет read-вызов
своим токеном и возвращает JSON.

Конфиг (env потребителя):
  SARAFAN_GATEWAY_KEY   API-ключ проекта (обязателен) — выдаёт владелец SARAFAN;
  SARAFAN_GATEWAY_URL   базовый URL шлюза (опц.; дефолт — прод-хост).

Запуск (stdio):
  SARAFAN_GATEWAY_KEY=... python -m gateway_mcp.server

Пример .mcp.json / claude config — в gateway_mcp/README.md.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from gateway_mcp.client import GatewayClient, GatewayError

mcp = FastMCP("vk_gateway_mcp")
_client = GatewayClient()

# Read-only annotations — общие для всех инструментов (шлюз только читает VK,
# идемпотентно, ходит во внешний сервис).
_READ_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}


def _format(result: Dict[str, Any]) -> str:
    """Сериализовать payload шлюза для агента (компактный, читаемый JSON)."""
    return json.dumps(result, ensure_ascii=False, indent=2)


def _handle_error(e: Exception) -> str:
    """Единое actionable-сообщение об ошибке для всех инструментов."""
    if isinstance(e, GatewayError):
        return f"Error: {e}"
    if isinstance(e, httpx.TimeoutException):
        return "Error: запрос к шлюзу истёк по таймауту. Повторите позже."
    if isinstance(e, httpx.HTTPError):
        return f"Error: сетевая ошибка обращения к шлюзу: {type(e).__name__}."
    return f"Error: неожиданная ошибка: {type(e).__name__}: {e}"


# --------------------------------------------------------------------------- #
# Входные модели
# --------------------------------------------------------------------------- #
class CommunityInput(BaseModel):
    """Параметры ``vk_get_community``."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    group: str = Field(
        ...,
        description="Числовой id или screen_name сообщества (напр. '1' или 'apiclub').",
        min_length=1,
        max_length=200,
    )
    fields: str = Field(
        default="",
        description=(
            "CSV доп. полей groups.getById (опц.). Пусто — дефолт шлюза "
            "(описание, members_count, activity, status, screen_name, фото, сайт, контакты)."
        ),
        max_length=500,
    )


class WallInput(BaseModel):
    """Параметры ``vk_get_wall``."""

    model_config = ConfigDict(extra="forbid")

    owner_id: int = Field(
        ...,
        description="ID владельца стены. Сообщество — со знаком минус (напр. -1 = vk.com/apiclub).",
    )
    count: int = Field(default=20, description="Сколько постов вернуть (1..100).", ge=1, le=100)
    offset: int = Field(default=0, description="Смещение для пагинации.", ge=0)


class CallInput(BaseModel):
    """Параметры ``vk_call`` — универсальный read-вызов."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    method: str = Field(
        ...,
        description=(
            "VK API read-метод из allowlist шлюза (напр. 'users.get', 'groups.getMembers', "
            "'wall.getComments', 'utils.resolveScreenName'). Запись запрещена → 400."
        ),
        min_length=1,
        max_length=64,
    )
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Параметры VK-метода как объект (напр. {'user_ids': '1,2', 'fields': 'city'}).",
    )


# --------------------------------------------------------------------------- #
# Инструменты
# --------------------------------------------------------------------------- #
@mcp.tool(
    name="vk_get_community", annotations={"title": "VK: инфо о сообществе", **_READ_ANNOTATIONS}
)
async def vk_get_community(params: CommunityInput) -> str:
    """Получить информацию о VK-сообществе через шлюз SARAFAN (``groups.getById``).

    Read-only. Удобная обёртка для частого кейса «что это за паблик».

    Args:
        params (CommunityInput):
            - group (str): числовой id или screen_name сообщества;
            - fields (str): CSV доп. полей (опц.).

    Returns:
        str: JSON ответа шлюза. Успех — ``{"ok": true, "response": [...]}``
        (массив groups.getById). Доменная VK-ошибка — ``{"ok": false, "error": {...}}``.
        Транспорт/auth/квота — строка ``"Error: ..."`` с подсказкой.
    """
    try:
        return _format(await _client.community(params.group, params.fields or None))
    except Exception as e:  # noqa: BLE001 — единый actionable-handler
        return _handle_error(e)


@mcp.tool(name="vk_get_wall", annotations={"title": "VK: посты со стены", **_READ_ANNOTATIONS})
async def vk_get_wall(params: WallInput) -> str:
    """Получить последние посты со стены VK через шлюз SARAFAN (``wall.get``).

    Read-only, с пагинацией (count/offset).

    Args:
        params (WallInput):
            - owner_id (int): владелец стены (сообщество — со знаком минус);
            - count (int): сколько постов (1..100, дефолт 20);
            - offset (int): смещение пагинации (дефолт 0).

    Returns:
        str: JSON ответа шлюза. Успех — ``{"ok": true, "response": {"count": N, "items": [...]}}``.
        Доменная VK-ошибка — ``{"ok": false, "error": {...}}``. Иначе ``"Error: ..."``.
    """
    try:
        return _format(await _client.wall(params.owner_id, params.count, params.offset))
    except Exception as e:  # noqa: BLE001 — единый actionable-handler
        return _handle_error(e)


@mcp.tool(name="vk_call", annotations={"title": "VK: произвольный read-метод", **_READ_ANNOTATIONS})
async def vk_call(params: CallInput) -> str:
    """Исполнить произвольный read-метод VK API из allowlist шлюза (``POST /call``).

    Универсальная дверь для методов без удобной обёртки (users.get,
    groups.getMembers, wall.getComments, utils.resolveScreenName и т.д.).
    Запись (wall.post/messages.send/likes.add/…) шлюзом запрещена → ``Error: 400``.

    Args:
        params (CallInput):
            - method (str): VK read-метод из allowlist;
            - params (dict): параметры метода.

    Returns:
        str: JSON ответа шлюза. Успех — ``{"ok": true, "response": <payload метода>}``.
        Доменная VK-ошибка — ``{"ok": false, "error": {...}}``. Иначе ``"Error: ..."``
        (в т.ч. 400, если метод вне read-allowlist).
    """
    try:
        return _format(await _client.call(params.method, params.params))
    except Exception as e:  # noqa: BLE001 — единый actionable-handler
        return _handle_error(e)


def main() -> None:
    """Запустить сервер по stdio (для локального подключения у потребителя)."""
    mcp.run()


if __name__ == "__main__":
    main()
