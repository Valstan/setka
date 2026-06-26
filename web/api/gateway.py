"""VK-шлюз (``/api/gateway``) — read-only ворота доступа в VK для других проектов.

Назначение. У владельца несколько проектов (@valstan). Когда другому проекту
(или его AI-сессии) нужно «сходить в VK — проанализировать сообщество, скачать
данные, импортировать на сайт», он упирается в закрытость VK 2026. SARAFAN —
внутренняя кухня VK (рабочие токены, клиент, smart-routing с cooldown,
per-token rate-limiter). Шлюз даёт это наружу безопасно.

Модель — «исполни и верни», НЕ «выдай токен». VK привязывает user-токен к IP
выпуска: чужой проект с нашим токеном со своего сервера получит ``error 5
(access_token was given to another ip address)``. Поэтому задачу исполняет
SARAFAN своим токеном, наружу уходит только результат.

Защита:
- авторизация — API-ключ на проект (``X-API-Key``, env ``GATEWAY_KEY_<PROJECT>``),
  сравнение constant-time; в логи пишем только имя проекта, не секрет;
- квота на ключ (:class:`modules.gateway.quota.GatewayQuota`) — чтобы один
  потребитель не выел общий VK-бюджет;
- самозащита токена — переиспользуем ``TokenPolicy`` (cooldown 5/17/29) +
  per-token rate-limiter внутри ``VKClient``;
- only-read allowlist (``GATEWAY_READ_METHODS``).

Auth-гейт приложения (session-cookie) шлюз НЕ трогает: ``/api/gateway`` в
``PUBLIC_PREFIXES`` middleware/auth_gate.py — у шлюза своя API-key защита.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from config.gateway import (
    GATEWAY_READ_METHODS,
    gateway_disabled,
    get_gateway_keys,
    get_gateway_quota_per_day,
    get_gateway_quota_per_min,
)
from database.connection import AsyncSessionLocal
from modules.vk_token_router import TokenOp, TokenPolicy

logger = logging.getLogger(__name__)
router = APIRouter()

# VK error codes, при которых токен ушёл в cooldown — пробуем следующий кандидат.
_TOKEN_COOLDOWN_CODES = (5, 17, 29)


# ---------------------------------------------------------------------------
# Зависимости: авторизация и квота
# ---------------------------------------------------------------------------
def require_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> str:
    """Проверить API-ключ; вернуть ИМЯ проекта (для квоты/логов).

    401 при отсутствии или несовпадении. Сравнение constant-time
    (``hmac.compare_digest``). Секрет в логи не попадает.
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key")
    for name, secret in get_gateway_keys().items():
        if secret and hmac.compare_digest(x_api_key, secret):
            return name
    logger.warning("gateway: rejected request with unknown API key")
    raise HTTPException(status_code=401, detail="Unknown API key")


def enforce_quota(key_name: str = Depends(require_api_key)) -> str:
    """Учесть запрос в квоте проекта; 429 + Retry-After при превышении."""
    from modules.gateway.quota import GatewayQuota
    from modules.vk_monitor.rate_limiter import _build_redis_client

    quota = GatewayQuota(
        _build_redis_client(),
        get_gateway_quota_per_min(),
        get_gateway_quota_per_day(),
    )
    allowed, retry_after = quota.check_and_consume(key_name)
    if not allowed:
        logger.info("gateway: quota exceeded for %s (retry_after=%ss)", key_name, retry_after)
        raise HTTPException(
            status_code=429,
            detail="Quota exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    return key_name


def _check_enabled() -> None:
    if gateway_disabled():
        raise HTTPException(status_code=503, detail="gateway disabled")


# ---------------------------------------------------------------------------
# Исполнитель read-вызова
# ---------------------------------------------------------------------------
async def _gateway_vk_read(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Исполнить read-вызов VK через ``TokenPolicy`` + ``VKClient``.

    Перебирает read-кандидатов: на cooldown-коде (5/17/29) фиксирует ошибку и
    пробует следующий токен; на иной VK-ошибке возвращает её как есть; на
    успехе сбрасывает счётчик ошибок и возвращает ``{"ok": True, "response": ...}``.

    ``VKClient.api_call`` синхронный и блокирует на per-token rate-limit —
    исполняем в пуле потоков (``asyncio.to_thread``), чтобы не вешать event loop.
    """
    from modules.vk_monitor.vk_client import VKClient

    async with AsyncSessionLocal() as session:
        policy = TokenPolicy(session)
        candidates = await policy.pick(TokenOp.READ)
        if not candidates:
            raise HTTPException(status_code=503, detail="no VK read-token available")

        last_error: Optional[Dict[str, Any]] = None
        for cand in candidates:
            client = VKClient(cand.token)
            result = await asyncio.to_thread(client.api_call, method, params)
            if isinstance(result, dict) and "error" in result:
                code = int(result["error"].get("error_code") or 0)
                await policy.report_error(cand.name, code)
                last_error = result["error"]
                if code in _TOKEN_COOLDOWN_CODES:
                    continue  # токен в cooldown → следующий кандидат
                return {"ok": False, "error": last_error}  # доменная VK-ошибка
            await policy.report_success(cand.name)
            return {"ok": True, "response": result}

        return {"ok": False, "error": last_error or {"error_msg": "all read-tokens failed"}}


# ---------------------------------------------------------------------------
# Эндпоинты
# ---------------------------------------------------------------------------
class GatewayCallIn(BaseModel):
    method: str = Field(..., min_length=1, max_length=64)
    params: Dict[str, Any] = Field(default_factory=dict)


@router.post("/call")
async def gateway_call(body: GatewayCallIn, key_name: str = Depends(enforce_quota)):
    """Универсальная дверь: исполнить любой read-метод VK из allowlist.

    Body: ``{"method": "wall.get", "params": {"owner_id": -1, "count": 5}}``.
    Метод вне ``GATEWAY_READ_METHODS`` → 400 (защита от write-вызовов).
    """
    _check_enabled()
    if body.method not in GATEWAY_READ_METHODS:
        raise HTTPException(status_code=400, detail=f"method not allowed: {body.method}")
    logger.info("gateway: %s called %s", key_name, body.method)
    return await _gateway_vk_read(body.method, body.params)


@router.get("/community")
async def gateway_community(
    group: str = Query(..., description="ID или screen_name сообщества"),
    fields: str = Query(
        "description,members_count,activity,status,screen_name,photo_200,site,contacts",
        description="CSV полей groups.getById",
    ),
    key_name: str = Depends(enforce_quota),
):
    """Инфо о сообществе (``groups.getById``) — удобная обёртка над /call."""
    _check_enabled()
    logger.info("gateway: %s community %s", key_name, group)
    return await _gateway_vk_read("groups.getById", {"group_ids": group, "fields": fields})


@router.get("/wall")
async def gateway_wall(
    owner_id: int = Query(..., description="ID владельца стены (сообщество — со знаком минус)"),
    count: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    key_name: str = Depends(enforce_quota),
):
    """Последние посты со стены (``wall.get``) — удобная обёртка над /call."""
    _check_enabled()
    logger.info("gateway: %s wall %s (count=%s)", key_name, owner_id, count)
    return await _gateway_vk_read(
        "wall.get", {"owner_id": owner_id, "count": count, "offset": offset}
    )
